import ssl


def ssl_context(conf, default_ssl_context_factory):
    contexto = default_ssl_context_factory()
    contexto.minimum_version = ssl.TLSVersion.TLSv1_2
    return contexto
