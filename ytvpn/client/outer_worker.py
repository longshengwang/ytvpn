import select
import logging
from enum import Enum
from common import connector
from common import forward_data
from common import forward_event
from common import ring_buffer
import data_handler
import time
logger = logging.getLogger('my_logger')


class OuterWorker(object):
    class State(Enum):
        NONE = 0
        LOGIN = 1
        WORKING = 3
        CLOSED = 4
        DONE = 5
    def __init__(self,outer_connector,sourth_interface_channel):
        self.__state = self.State.NONE
        self.__data_handler = data_handler.OuterDataHandler()
        self.__connector = outer_connector
        self.__sourth_interface_channel = sourth_interface_channel
        self.__ring_buffer = ring_buffer.TimeoutRingbuffer(10240 * 10240, 5)
        self.__worker_id = None

    def has_done(self):
        return self.__state == self.State.DONE

    def __north_interface_event(self, event):
        if self.__state in (self.State.LOGIN, self.State.WORKING):
            self.__handle_working_event(event)

    def __handle_data(self):
        datas = self.__data_handler.get_forward_datas(self.__ring_buffer)

        if len(datas) <= 0:
            return

        if self.__state == self.State.LOGIN:
            for data in datas:
                if data.data_type == forward_data.DATA_TYPE.LOGIN:
                    self.__worker_id = data.id
                    allocate_ip_event = forward_event.AllocateIPEvent(data.dst_ip)
                    self.__sourth_interface_channel(allocate_ip_event)
                    self.__state = self.State.WORKING
                    logger.info('OuterWorker change state from LOGIN to WORKING')
                elif data.data_type == forward_data.DATA_TYPE.LOGIN_FAILED:
                    logger.error('OuterWorker login to server failed,change state to CLOSED')
                    self.__state = self.State.CLOSED
        elif self.__state == self.State.WORKING:
            for data in datas:
                if data.data_type == forward_data.DATA_TYPE.TRANS_DATA:
                    trans_event = forward_event.TransDataEvent(data.id, data)
                    self.__sourth_interface_channel(trans_event)


    def __scheduler_event(self, event):
        if not isinstance(event,forward_event.SchedulerEvent):
            return
        if self.__state == self.State.NONE:
            #self.__state = self.State.LOGIN
            self.__data_handler.login_to_server('cyt','123456',self.__connector)
            self.__state = self.State.LOGIN
        elif self.__state == self.State.WORKING:
            if self.__connector.con_state != connector.CON_STATE.CON_CONNECTED:
                self.__state = self.State.CLOSED
                logger.debug("OuterWorker current state:WORKING change state to CLOSED due connector state error:%s"%(str(self.__connector.con_state)) )
                return
        elif self.__state == self.State.CLOSED:
            self.__connector.close()
            self.__state = self.State.DONE
            logger.debug("OuterWorker current state:CLOSED change state to DONE")
            return
        self.__handle_data()

    def __sourth_interface_transdata_event(self, event):
        if not isinstance(event,forward_event.TransDataEvent):
            return

        f_data = event.forward_data
        if event.forward_data.data_type == forward_data.DATA_TYPE.TRANS_DATA:
            f1_time = time.time()
            self.__data_handler.trans_data(self.__worker_id, f_data.data, self.__connector)
            f2_time = time.time()
            print 'send len:%d use_time:%.06f'%(len(f_data.data),(f2_time-f1_time)*1000)

    def __handle_working_event(self, event):
        error_happen = False
        if event.fd_event & select.EPOLLIN:
            recv_msg = self.__connector.recv()
            if len(recv_msg) > 0:
                # pass data
                self.__ring_buffer.put(bytearray(recv_msg))
                # self.__ring_buffer.print_buf()
            else:
                if self.__connector.con_state != connector.CON_STATE.CON_CONNECTED:
                    error_happen = True
                    logger.error("OuterWorker current state:WORKING recv data error")

        elif event.fd_event & select.EPOLLHUP:
            error_happen = True

        if error_happen:
            self.__state = self.State.CLOSED
            logger.debug("OuterWorkercurrent state:WORKING change state to DISCONNECTED")

    def __sourth_interface_closecon_event(self, event):
        if not isinstance(event,forward_event.CloseConEvent):
            return
        self.__state = self.State.CLOSED

    @forward_event.event_filter
    def handler_event(self, event):
        if event.event_type == forward_event.FDEVENT:
            # socket receive msg
            self.__north_interface_event(event)
        elif event.event_type == forward_event.TRANSDATAEVENT:
            self.__sourth_interface_transdata_event(event)
        elif event.event_type == forward_event.CLOSECONEVENT:
            self.__sourth_interface_closecon_event(event)
        elif event.event_type == forward_event.SCHEDULEREVENT:
            self.__scheduler_event(event)