===========================
Concurrent (``concurrent``)
===========================

.. automodule:: orangecanvas.utils.concurrent


.. autoclass:: orangecanvas.utils.concurrent.ThreadPoolExecutor
   :members:
   :member-order: bysource
   :show-inheritance:


.. autoclass:: orangecanvas.utils.concurrent.FutureWatcher
   :members:
   :exclude-members:
      done,
      finished,
      cancelled,
      resultReady,
      exceptionReady
   :member-order: bysource
   :show-inheritance:

   .. autoattribute:: done(future: Future)
   .. autoattribute:: finished(future: Future)
   .. autoattribute:: cancelled(future: Future)
   .. autoattribute:: resultReady(result: object)
   .. autoattribute:: exceptionReady(result: BaseException)


.. autofunction:: orangecanvas.utils.concurrent.submit
