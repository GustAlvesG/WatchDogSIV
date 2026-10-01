import logging

# Evita que os testes escrevam no log.log do serviço: com um handler já registrado,
# o Utils.py não cria os handlers de arquivo/console.
_logger = logging.getLogger("watchdog_lpr")
_logger.addHandler(logging.NullHandler())
_logger.propagate = False
