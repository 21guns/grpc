# Copyright 2015, Google Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
#     * Redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above
# copyright notice, this list of conditions and the following disclaimer
# in the documentation and/or other materials provided with the
# distribution.
#     * Neither the name of Google Inc. nor the names of its
# contributors may be used to endorse or promote products derived from
# this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""Entry points into GRPC."""

import threading

from grpc._adapter import fore as _fore
from grpc._adapter import rear as _rear
from grpc.early_adopter import _face_utilities
from grpc.early_adopter import _reexport
from grpc.early_adopter import interfaces
from grpc.framework.base import util as _base_utilities
from grpc.framework.base.packets import implementations as _tickets_implementations
from grpc.framework.face import implementations as _face_implementations
from grpc.framework.foundation import logging_pool

_THREAD_POOL_SIZE = 80
_ONE_DAY_IN_SECONDS = 24 * 60 * 60


class _Server(interfaces.Server):

  def __init__(self, breakdown, port, private_key, certificate_chain):
    self._lock = threading.Lock()
    self._breakdown = breakdown
    self._port = port
    if private_key is None or certificate_chain is None:
      self._key_chain_pairs = ()
    else:
      self._key_chain_pairs = ((private_key, certificate_chain),)

    self._pool = None
    self._back = None
    self._fore_link = None

  def _start(self):
    with self._lock:
      if self._pool is None:
        self._pool = logging_pool.pool(_THREAD_POOL_SIZE)
        servicer = _face_implementations.servicer(
            self._pool, self._breakdown.implementations, None)
        self._back = _tickets_implementations.back(
            servicer, self._pool, self._pool, self._pool, _ONE_DAY_IN_SECONDS,
            _ONE_DAY_IN_SECONDS)
        self._fore_link = _fore.ForeLink(
            self._pool, self._breakdown.request_deserializers,
            self._breakdown.response_serializers, None, self._key_chain_pairs)
        self._back.join_fore_link(self._fore_link)
        self._fore_link.join_rear_link(self._back)
        self._fore_link.start()
      else:
        raise ValueError('Server currently running!')

  def _stop(self):
    with self._lock:
      if self._pool is None:
        raise ValueError('Server not running!')
      else:
        self._fore_link.stop()
        _base_utilities.wait_for_idle(self._back)
        self._pool.shutdown(wait=True)
        self._fore_link = None
        self._back = None
        self._pool = None

  def __enter__(self):
    self._start()
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    self._stop()
    return False

  def start(self):
    self._start()

  def stop(self):
    self._stop()

  def port(self):
    with self._lock:
      return self._fore_link.port()


class _Stub(interfaces.Stub):

  def __init__(
      self, breakdown, host, port, secure, root_certificates, private_key,
      certificate_chain, server_host_override=None):
    self._lock = threading.Lock()
    self._breakdown = breakdown
    self._host = host
    self._port = port
    self._secure = secure
    self._root_certificates = root_certificates
    self._private_key = private_key
    self._certificate_chain = certificate_chain
    self._server_host_override = server_host_override

    self._pool = None
    self._front = None
    self._rear_link = None
    self._understub = None

  def __enter__(self):
    with self._lock:
      if self._pool is None:
        self._pool = logging_pool.pool(_THREAD_POOL_SIZE)
        self._front = _tickets_implementations.front(
            self._pool, self._pool, self._pool)
        self._rear_link = _rear.RearLink(
            self._host, self._port, self._pool,
            self._breakdown.request_serializers,
            self._breakdown.response_deserializers, self._secure,
            self._root_certificates, self._private_key, self._certificate_chain,
            server_host_override=self._server_host_override)
        self._front.join_rear_link(self._rear_link)
        self._rear_link.join_fore_link(self._front)
        self._rear_link.start()
        self._understub = _face_implementations.dynamic_stub(
            _reexport.common_cardinalities(self._breakdown.cardinalities),
            self._front, self._pool, '')
      else:
        raise ValueError('Tried to __enter__ already-__enter__ed Stub!')
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    with self._lock:
      if self._pool is None:
        raise ValueError('Tried to __exit__ non-__enter__ed Stub!')
      else:
        self._rear_link.stop()
        _base_utilities.wait_for_idle(self._front)
        self._pool.shutdown(wait=True)
        self._rear_link = None
        self._front = None
        self._pool = None
        self._understub = None
    return False

  def __getattr__(self, attr):
    with self._lock:
      if self._pool is None:
        raise ValueError('Tried to __getattr__ non-__enter__ed Stub!')
      else:
        underlying_attr = getattr(self._understub, attr, None)
        method_cardinality = self._breakdown.cardinalities.get(attr)
        # TODO(nathaniel): Eliminate this trick.
        if underlying_attr is None:
          for method_name, method_cardinality in self._breakdown.cardinalities.iteritems():
            last_slash_index = method_name.rfind('/')
            if 0 <= last_slash_index and method_name[last_slash_index + 1:] == attr:
              underlying_attr = getattr(self._understub, method_name)
              break
          else:
            raise AttributeError(attr)
        if method_cardinality is interfaces.Cardinality.UNARY_UNARY:
          return _reexport.unary_unary_sync_async(underlying_attr)
        elif method_cardinality is interfaces.Cardinality.UNARY_STREAM:
          return lambda request, timeout: _reexport.cancellable_iterator(
              underlying_attr(request, timeout))
        elif method_cardinality is interfaces.Cardinality.STREAM_UNARY:
          return _reexport.stream_unary_sync_async(underlying_attr)
        elif method_cardinality is interfaces.Cardinality.STREAM_STREAM:
          return lambda request_iterator, timeout: (
              _reexport.cancellable_iterator(underlying_attr(
                  request_iterator, timeout)))
        else:
          raise AttributeError(attr)


def _build_stub(
    methods, host, port, secure, root_certificates, private_key,
    certificate_chain, server_host_override=None):
  breakdown = _face_utilities.break_down_invocation(methods)
  return _Stub(
      breakdown, host, port, secure, root_certificates, private_key,
      certificate_chain, server_host_override=server_host_override)


def _build_server(methods, port, private_key, certificate_chain):
  breakdown = _face_utilities.break_down_service(methods)
  return _Server(breakdown, port, private_key, certificate_chain)


def insecure_stub(methods, host, port):
  """Constructs an insecure interfaces.Stub.

  Args:
    methods: A dictionary from RPC method name to
      interfaces.RpcMethodInvocationDescription describing the RPCs to be
      supported by the created stub.
    host: The host to which to connect for RPC service.
    port: The port to which to connect for RPC service.

  Returns:
    An interfaces.Stub affording RPC invocation.
  """
  return _build_stub(methods, host, port, False, None, None, None)


def secure_stub(
    methods, host, port, root_certificates, private_key, certificate_chain,
    server_host_override=None):
  """Constructs an insecure interfaces.Stub.

  Args:
    methods: A dictionary from RPC method name to
      interfaces.RpcMethodInvocationDescription describing the RPCs to be
      supported by the created stub.
    host: The host to which to connect for RPC service.
    port: The port to which to connect for RPC service.
    root_certificates: The PEM-encoded root certificates or None to ask for
      them to be retrieved from a default location.
    private_key: The PEM-encoded private key to use or None if no private key
      should be used.
    certificate_chain: The PEM-encoded certificate chain to use or None if no
      certificate chain should be used.
    server_host_override: (For testing only) the target name used for SSL
      host name checking.

  Returns:
    An interfaces.Stub affording RPC invocation.
  """
  return _build_stub(
      methods, host, port, True, root_certificates, private_key,
      certificate_chain, server_host_override=server_host_override)


def insecure_server(methods, port):
  """Constructs an insecure interfaces.Server.

  Args:
    methods: A dictionary from RPC method name to
      interfaces.RpcMethodServiceDescription describing the RPCs to
      be serviced by the created server.
    port: The desired port on which to serve or zero to ask for a port to
      be automatically selected.

  Returns:
    An interfaces.Server that will run with no security and
      service unsecured raw requests.
  """
  return _build_server(methods, port, None, None)


def secure_server(methods, port, private_key, certificate_chain):
  """Constructs a secure interfaces.Server.

  Args:
    methods: A dictionary from RPC method name to
      interfaces.RpcMethodServiceDescription describing the RPCs to
      be serviced by the created server.
    port: The port on which to serve or zero to ask for a port to be
      automatically selected.
    private_key: A pem-encoded private key.
    certificate_chain: A pem-encoded certificate chain.

  Returns:
    An interfaces.Server that will serve secure traffic.
  """
  return _build_server(methods, port, private_key, certificate_chain)
