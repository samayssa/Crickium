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
    """Decorator for game callbacks with inactivity-clock reconciliation."""
    def decorator(fn):
        import functools

        @functools.wraps(fn)
        async def wrapped(callback_query, *args, **kwargs):
            engine = None
            match_id = None
            if action.startswith("playipl_"):
                engine = "PLAYIPL"
            elif action.startswith("playint_"):
                engine = "PLAYINT"
            elif action.startswith("play_"):
                engine = "PLAY"

            if engine:
                try:
                    parts = str(callback_query.get("data") or "").split(":")
                    if len(parts) > 1 and parts[1].isdigit():
                        match_id = int(parts[1])
                except Exception:
                    match_id = None

            before = None
            actor_id = int((callback_query.get("from") or {}).get("id") or 0)
            if engine and match_id is not None:
                try:
                    from utils.game_inactivity import cancel, game_signature
                    before = await game_signature(engine, match_id)
                    # The user has actively touched the game. Stop their timer
                    # before running any DB/Telegram work so a boundary-second
                    # race cannot declare them a loser while their callback is
                    # still being processed. The post-sync below restores or
                    # advances the timer according to the resulting state.
                    cancel(engine, match_id, actor_id)
                except Exception as exc:
                    print(f"[registry] inactivity pre-signature/cancel failed for {action}: {exc!r}")

            try:
                result = await fn(callback_query, *args, **kwargs)
            except Exception:
                if engine and match_id is not None:
                    try:
                        from utils.game_inactivity import sync_after_change
                        await sync_after_change(engine, match_id, actor_id)
                    except Exception as exc:
                        print(f"[registry] inactivity recovery failed for {action}: {exc!r}")
                raise

            if engine and match_id is not None:
                try:
                    from utils.game_inactivity import game_signature, sync_after_change
                    after = await game_signature(engine, match_id)
                    await sync_after_change(engine, match_id, actor_id)
                except Exception as exc:
                    print(f"[registry] inactivity post-sync failed for {action}: {exc!r}")

            return result

        print(f"[handlers/registry] Registering callback action '{action}' -> {fn.__name__}")
        CALLBACKS[action] = wrapped
        return wrapped
    return decorator
