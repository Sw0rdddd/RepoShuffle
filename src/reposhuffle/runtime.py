"""Self-contained runtime copied into transformed packages (stdlib only).

Logical module names remain public; only their source locations change. This
finder never consults the input repository and never patches filesystem APIs.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import pkgutil
import sys
from pathlib import Path

try:
    from importlib.resources.readers import MultiplexedPath
except ImportError:  # Python 3.10
    from importlib.readers import MultiplexedPath

try:
    from importlib.resources.abc import TraversableResources
except ImportError:  # Python 3.10
    from importlib.abc import TraversableResources


class AliasLoader(importlib.abc.Loader):
    """A physical import reuses the canonical module without executing it twice."""

    def __init__(self, canonical):
        self.canonical = canonical

    def create_module(self, spec):
        module = importlib.import_module(self.canonical)
        self.spec = module.__spec__
        return module

    def exec_module(self, module):
        # importlib assigns the alias spec even when create_module returns an
        # existing module. Keep introspection and reload on the canonical name.
        module.__spec__ = self.spec


class AliasFinder(importlib.abc.MetaPathFinder):
    def __init__(self, aliases):
        self.aliases = aliases

    def find_spec(self, fullname, path=None, target=None):
        canonical = self.aliases.get(fullname)
        if canonical is None:
            return None
        return importlib.util.spec_from_loader(fullname, AliasLoader(canonical))


def install_aliases(storage, aliases, logical_storage=None):
    prefix = (
        storage[: -len(logical_storage)]
        if logical_storage and storage.endswith(logical_storage)
        else ""
    )
    sys.meta_path.insert(
        0, AliasFinder({storage + "." + suffix: prefix + name for suffix, name in aliases.items()})
    )


class ResourceReader(TraversableResources):
    def __init__(self, directory):
        self.directory = directory

    def files(self):
        if isinstance(self.directory, RoutedNamespacePath):
            return MultiplexedPath(*self.directory)
        return Path(self.directory)


class RoutedNamespacePath:
    """Keep logical resources and recompute external portions after path changes."""

    def __init__(self, package, directory):
        self.package = package
        self.directory = str(directory)
        self.extra = []

    def _paths(self):
        parent, _, child = self.package.rpartition(".")
        search = getattr(sys.modules.get(parent), "__path__", []) if parent else sys.path
        paths = [self.directory, *self.extra]
        for base in search:
            candidate = Path(base) / child
            if candidate.is_dir() and not (candidate / "__init__.py").is_file():
                paths.append(str(candidate))
        return list(dict.fromkeys(paths))

    def __iter__(self):
        return iter(self._paths())

    def __len__(self):
        return len(self._paths())

    def __getitem__(self, index):
        return self._paths()[index]

    def append(self, path):
        self.extra.append(path)


class NamespaceLoader(importlib.abc.Loader):
    """A routed namespace has no initializer or file, but does have resources."""

    def __init__(self, resources):
        self.resources = resources

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        pass

    def get_resource_reader(self, fullname):
        return ResourceReader(self.resources)


class MappedLoader(importlib.machinery.SourceFileLoader):
    def __init__(self, name, filename, package, resources):
        super().__init__(name, filename)
        self.package = package
        self.resources = Path(resources)

    def is_package(self, fullname):
        return self.package

    def get_resource_reader(self, fullname):
        return ResourceReader(self.resources) if self.package else None

    def get_data(self, path):
        actual = Path(path)
        if actual != Path(self.path):
            try:
                relative = actual.relative_to(Path(self.path).parent)
            except ValueError:
                pass
            else:
                resource = self.resources / relative
                if resource.is_file():
                    return resource.read_bytes()
        return super().get_data(path)


class PackagePath(importlib.abc.PathEntryFinder):
    def __init__(self, finder, package, directory):
        self.finder = finder
        self.package = package
        self.directory = str(directory)
        self.disk = importlib.machinery.FileFinder(
            self.directory,
            (importlib.machinery.ExtensionFileLoader, importlib.machinery.EXTENSION_SUFFIXES),
            (importlib.machinery.SourceFileLoader, importlib.machinery.SOURCE_SUFFIXES),
            (importlib.machinery.SourcelessFileLoader, importlib.machinery.BYTECODE_SUFFIXES),
        )

    def find_spec(self, fullname, target=None):
        return self.finder.find_spec(fullname) or self.disk.find_spec(fullname, target)

    def invalidate_caches(self):
        self.disk.invalidate_caches()

    def iter_modules(self, prefix=""):
        found = {}
        logical_prefix = self.package + "."
        for name, entry in self.finder.routes.items():
            if name.startswith(logical_prefix):
                child = name[len(logical_prefix) :]
                if "." not in child:
                    found[child] = entry[1]
        for name, package in pkgutil.iter_importer_modules(self.disk):
            if not name.startswith(self.finder.private_prefix):
                found.setdefault(name, package)
        yield from ((prefix + name, package) for name, package in sorted(found.items()))


class ModuleFinder(importlib.abc.MetaPathFinder):
    def __init__(self, root, base, routes, private_prefix):
        self.root = root
        self.base = Path(base)
        self.routes = routes
        self.private_prefix = private_prefix

    def path(self, package, directory, namespace=False):
        path = str(directory)
        sys.path_importer_cache[path] = PackagePath(self, package, directory)
        if namespace:
            return RoutedNamespacePath(package, directory)
        previous = getattr(sys.modules.get(package), "__path__", [])
        return list(dict.fromkeys([path, *previous]))

    def find_spec(self, fullname, path=None, target=None):
        entry = self.routes.get(fullname)
        if entry is None:
            return None
        relative, package, resource_directory = entry
        resources = self.base / resource_directory
        if relative is None:
            locations = self.path(fullname, resources, namespace=True)
            spec = importlib.machinery.ModuleSpec(
                fullname, NamespaceLoader(locations), is_package=True
            )
        else:
            filename = str(self.base / relative)
            loader = MappedLoader(fullname, filename, package, resources)
            spec = importlib.util.spec_from_file_location(fullname, filename, loader=loader)
        if package:
            spec.submodule_search_locations = (
                locations if relative is None else self.path(fullname, resources)
            )
        return spec


def install(root, package_file, routes, private_prefix, logical_root=None):
    base = Path(package_file).resolve().parent
    if logical_root and root != logical_root:
        routes = {root + name[len(logical_root) :]: entry for name, entry in routes.items()}
    for finder in sys.meta_path:
        if isinstance(finder, ModuleFinder) and finder.root == root and finder.base == base:
            return
    finder = ModuleFinder(root, base, routes, private_prefix)
    sys.meta_path.insert(0, finder)
    module = sys.modules[root]
    module.__path__ = finder.path(root, base)
    # SourceFileLoader still points to the real root initializer; resources stay here.
