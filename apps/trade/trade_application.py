import logging
import zmq

from tornado.options import  options

from sqlalchemy.orm import scoped_session, sessionmaker

from errors import *

class TradeApplication(object):

  @classmethod
  def instance(cls):
    if not hasattr(cls, "_instance"):
      cls._instance = cls()
    return cls._instance

  def initialize(self):
    self.options = options

    from models import engine
    self.db_session = scoped_session(sessionmaker(bind=engine))

    from session_manager import SessionManager
    self.session_manager = SessionManager(timeout_limit=self.options.session_timeout_limit)

    self.context = zmq.Context()
    self.input_socket = self.context.socket(zmq.REP)
    self.input_socket.bind(self.options.trade_in)

    self.publisher_socket = self.context.socket(zmq.PUB)
    self.publisher_socket.bind(self.options.trade_pub)


    input_log_file_handler = logging.handlers.TimedRotatingFileHandler( self.options.trade_log, when='MIDNIGHT')
    formatter = logging.Formatter('%(asctime)s - %(message)s')
    input_log_file_handler.setFormatter(formatter)

    self.replay_logger = logging.getLogger("REPLAY")
    self.replay_logger.setLevel(logging.INFO)
    self.replay_logger.addHandler(input_log_file_handler)
    self.replay_logger.info('START')

    self.publish_queue = []

    self.log_start_data()

  def log(self, command, key, value=None):
    log_msg = command + ',' + key
    if value:
      log_msg += ',' + str(value)
    self.replay_logger.info(  log_msg )

  def log_start_data(self):
    self.log('PARAM','BEGIN')
    self.log('PARAM','trade_in'              ,self.options.trade_in)
    self.log('PARAM','trade_pub'             ,self.options.trade_pub)
    self.log('PARAM','trade_log'             ,self.options.trade_log)
    self.log('PARAM','session_timeout_limit' ,self.options.session_timeout_limit)
    self.log('PARAM','db_echo'               ,self.options.db_echo)
    self.log('PARAM','db_engine'             ,self.options.db_engine)
    self.log('PARAM','END')

    from models import User, BoletoOptions, Order

    # log all users on the replay log
    users = self.db_session.query(User)
    for user in users:
      self.log('DB_ENTITY', 'USER', user)

    boleto_options = self.db_session.query(BoletoOptions)
    for boleto in boleto_options:
      self.log('DB_ENTITY', 'BOLETO',  boleto)

    orders = self.db_session.query(Order).filter(Order.status.in_(("0", "1"))).order_by(Order.created)
    for order in orders:
      self.log('DB_ENTITY','ORDER',order)
      #OrderMatcher.get( order.symbol  ).match(self.session, order)

  def publish(self, key, data):
    self.publish_queue.append([ key, data ])

  def run(self):
    from bitex.message import JsonMessage


    while True:
      raw_message = self.input_socket.recv()

      msg_header              = raw_message[:3]
      session_id              = raw_message[4:20]
      json_raw_message        = raw_message[21:].strip()

      try:
        msg = None
        if json_raw_message:
          msg = JsonMessage(json_raw_message)
          if not msg.is_valid():
            self.log('IN', 'TRADE_IN_REQ',  raw_message)
            raise InvalidMessageError()
          else:
            # never write passwords in the log file
            if msg.has('Password'):
              raw_message = raw_message.replace(msg.get('Password'), '*')
            if msg.has('NewPassword'):
              raw_message = raw_message.replace(msg.get('NewPassword'), '*')

        self.log('IN', 'TRADE_IN_REQ' ,raw_message )

        response_message = self.session_manager.process_message( msg_header, session_id, msg )

      except TradeRuntimeError, e:
        self.session_manager.close_session(session_id)
        response_message = 'ERR,{"MsgType":"ERROR", "Description":"' + e.error_description + '", "Detail": ""}'

      except Exception,e:
        self.session_manager.close_session(session_id)
        response_message = 'ERR,{"MsgType":"ERROR", "Description":"Unknow error", "Detail": "'  + str(e) + '"}'

      # send the response
      self.log('OUT', 'TRADE_IN_REP', response_message )
      self.input_socket.send_unicode(response_message)

      # publish all publications
      for key, message in self.publish_queue:
        self.log('OUT', 'TRADE_PUB', str([key, message]) )
        self.publisher_socket.send_multipart( [str(key), str(message)] )
      self.publish_queue = []

application = TradeApplication.instance()