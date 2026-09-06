import logging.config
import uuid



def join_extras(*dict_args):
    """
        Given any number of dicts, shallow copy and merge into a new dict,
        precedence goes to key value pairs in latter dicts.
        """
    result = {}
    for dictionary in dict_args:
        result.update(dictionary)
    return result


def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[-1].strip()
    elif request.META.get('HTTP_X_REAL_IP'):
        ip = request.META.get('HTTP_X_REAL_IP')
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def user_extra(request):
    extras = {
        "userinfo": {
            "username": request.user,
            "ip-addr": get_client_ip(request),
            "session-id": request.session.session_key,
        },
        "path": request.path,
    }
    return extras

