import json
import socket
import time
import os
import logging


def print_dict(d):
    print(json.dumps(d, indent=2))


class JSONRPCException(Exception):
    def __init__(self, message):
        self.message = message


class JSONRPCClient(object):
    def __init__(self, addr, port=None, timeout=60.0, **kwargs):
        self.sock = None
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        ch.setLevel(logging.DEBUG)
        self._logger = logging.getLogger("JSONRPCClient(%s)" % addr)
        self._logger.addHandler(ch)
        self.set_log_level(kwargs.get('log_level', logging.ERROR))

        self.timeout = timeout
        self._request_id = 0
        self._recv_buf = ""
        self._reqs = []
        try:
            if os.path.exists(addr):
                self._logger.debug("Trying to connect to UNIX socket: %s", addr)
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.connect(addr)
            elif ':' in addr:
                self._logger.debug("Trying to connect to IPv6 address addr:%s, port:%i", addr, port)
                for res in socket.getaddrinfo(addr, port, socket.AF_INET6, socket.SOCK_STREAM, socket.SOL_TCP):
                    af, socktype, proto, canonname, sa = res
                self.sock = socket.socket(af, socktype, proto)
                self.sock.connect(sa)
            else:
                self._logger.debug("Trying to connect to IPv4 address addr:%s, port:%i'", addr, port)
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.connect((addr, port))
        except socket.error as ex:
            raise JSONRPCException("Error while connecting to %s\n"
                                   "Error details: %s" % (addr, ex))

    def get_logger(self):
        return self._logger

    """Set logging level

    Args:
        lvl: Log level to set as accepted by logger.setLevel
    """
    def set_log_level(self, lvl):
        self._logger.info("Setting log level to %s", lvl)
        self._logger.setLevel(lvl)
        self._logger.info("Log level set to %s", lvl)

    def __del__(self):
        if getattr(self, "sock", None):
            self.sock.close()

    def send(self, method, params=None):
        self._request_id += 1
        req = {
            'jsonrpc': '2.0',
            'method': method,
            'id': self._request_id
        }

        if params:
            req['params'] = params

        reqstr = json.dumps(req,  indent=2)
        self._logger.info("request:\n%s\n", reqstr)
        self.sock.sendall(reqstr.encode("utf-8"))
        return req

    def decode_one_response(self):
        try:
            self._logger.debug("Trying to decode response '%s'", self._recv_buf)
            buf = self._recv_buf.lstrip()
            obj, idx = json.JSONDecoder().raw_decode(buf)
            self._recv_buf = buf[idx:]
            return obj
        except ValueError:
            self._logger.debug("Partial response")
            return None

    def recv(self):
        start_time = time.clock()
        response = self.decode_one_response()
        while not response:
            try:
                timeout = self.timeout - (time.clock() - start_time)
                self.sock.settimeout(timeout)
                newdata = self.sock.recv(4096)
                if not newdata:
                    self.sock.close()
                    self.sock = None
                    raise JSONRPCException("Connection closed with partial response:\n%s\n" % self._recv_buf)
                self._recv_buf += newdata.decode("utf-8")
                response = self.decode_one_response()
            except socket.timeout:
                break  # throw exception after loop to avoid Python freaking out about nested exceptions
            except ValueError:
                continue  # incomplete response; keep buffering

        if not response:
            raise JSONRPCException("Timeout while waiting for response:\n%s\n" % self._recv_buf)

        self._logger.info("response:\n%s\n", json.dumps(response, indent=2))
        if 'error' in response:
            msg = "\n".join(["Got JSON-RPC error response",
                             "response:",
                             json.dumps(response['error'], indent=2)])
            raise JSONRPCException(msg)
        return response

    def call(self, method, params=None):
        self._logger.debug("call('%s')" % method)
        self.send(method, params)
        try:
            return self.recv()['result']
        except JSONRPCException as e:
            """ Don't expect response to kill """
            if not self.sock and method == "kill_instance":
                self._logger.info("Connection terminated but ignoring since method is '%s'" % method)
                return {}
            else:
                raise e
