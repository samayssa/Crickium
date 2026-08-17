COMMANDS = {}
CALLBACKS = {}


def register(command_name):
    """Decorator: registers an async function to handle a given /command."""
    def decorator(fn):
        print(f"[handlers/registry] Registering command '/{command_name}' -> {fn.__name__}")
        COMMANDS[command_name] = fn
        return fn
    return decorator


def register_callback(action):
    """Decorator: registers an async function to handle a button press whose
    callback_data starts with '{action}:'. E.g. action='challenge_accept'
    matches callback_data 'challenge_accept:42'."""
    def decorator(fn):
        print(f"[handlers/registry] Registering callback action '{action}' -> {fn.__name__}")
        CALLBACKS[action] = fn
        return fn
    return decorator
