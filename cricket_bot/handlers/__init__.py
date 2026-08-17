import importlib
import pkgutil

print(f"[handlers/__init__.py] Scanning for handler modules in: {list(__path__)}")

_found = [m for m in pkgutil.iter_modules(__path__) if m.name != "registry"]
print(f"[handlers/__init__.py] Found {len(_found)} module(s): {[m.name for m in _found]}")

for module in _found:
    print(f"[handlers/__init__.py] Importing {__name__}.{module.name} ...")
    importlib.import_module(f"{__name__}.{module.name}")
    print(f"[handlers/__init__.py] Imported {__name__}.{module.name} successfully.")

print("[handlers/__init__.py] All handler modules loaded.")
