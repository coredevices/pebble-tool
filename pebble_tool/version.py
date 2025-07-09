version_base = (5, 0, 2)
version_suffix = None

if version_suffix is None:
    __version_info__ = version_base
else:
    __version_info__ = version_base + (version_suffix,)

__version__ = '{}.{}'.format(*version_base)
if version_base[2] != 0:
    __version__ += '.{}'.format(version_base[2])

if version_suffix is not None:
    __version__ += '-{}'.format(version_suffix)
