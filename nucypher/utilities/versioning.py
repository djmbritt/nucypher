"""
 This file is part of nucypher.

 nucypher is free software: you can redistribute it and/or modify
 it under the terms of the GNU Affero General Public License as published by
 the Free Software Foundation, either version 3 of the License, or
 (at your option) any later version.

 nucypher is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 GNU Affero General Public License for more details.

 You should have received a copy of the GNU Affero General Public License
 along with nucypher.  If not, see <https://www.gnu.org/licenses/>.
"""


from abc import abstractmethod, ABC
from typing import Dict, Tuple, Callable


class Versioned(ABC):
    """Base class for serializable entities"""

    _BRAND_LENGTH = 2  # bytes
    _VERSION_LENGTH = 2
    _HEADER_SIZE = _BRAND_LENGTH + _VERSION_LENGTH

    class InvalidHeader(ValueError):
        """
        Raised when an unexpected or invalid bytes header is
        encountered during deserialization.
        """

    class Empty(ValueError):
        """Raised when 0 bytes are remaining after parsing the header."""

    @classmethod
    @abstractmethod
    def _brand(cls) -> bytes:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def _version(cls) -> int:
        raise NotImplementedError

    def __init_subclass__(cls, **kwargs):
        if len(cls._brand()) != cls._BRAND_LENGTH:
            raise cls.InvalidHeader("Brand must be exactly two bytes.")
        if not cls._brand().isalpha():
            raise cls.InvalidHeader("Brand must be alphanumeric.")
        brands = tuple(v._brand() for v in Versioned.__subclasses__())
        if len(brands) != len(set(brands)):
            raise cls.InvalidHeader(f"Duplicated_brand {cls._brand()}.")

    #
    # Serialize
    #

    def __bytes__(self) -> bytes:
        return self._header() + self._payload()

    @classmethod
    def _header(cls) -> bytes:
        """The entire bytes header to prepend to the instance payload."""
        version_bytes = cls._version().to_bytes(cls._VERSION_LENGTH, 'big')
        return cls._brand() + version_bytes

    @abstractmethod
    def _payload(self) -> bytes:
        """The unbranded and unversioned bytes-serialized representation of this instance."""
        raise NotImplementedError

    #
    # Deserialize
    #

    @classmethod
    @abstractmethod
    def _from_bytes_current(cls, data):
        """The current deserializer"""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def _old_version_handlers(cls) -> Dict:
        """Old deserializer callables keyed by version."""
        raise NotImplementedError

    @classmethod
    def from_bytes(cls, data: bytes):
        """"Public deserialization API"""
        brand, version, payload = cls._parse(data)
        handlers = cls._deserializers()
        return handlers[version](payload)

    @classmethod
    def _parse(cls, data: bytes) -> Tuple[bytes, int, bytes]:
        """Parse bytes in"""
        brand, version = cls._parse_brand(data), cls._parse_version(data)
        payload = data[cls._HEADER_SIZE:]
        if len(payload) == 0:
            raise cls.Empty('No content to deserialize.')
        return brand, version, payload

    @classmethod
    def _parse_brand(cls, data: bytes) -> bytes:
        brand = data[:cls._BRAND_LENGTH]
        if brand != cls._brand():
            error = f"Incorrect brand. Expected {cls._brand()}, Got {brand}."
            if not brand.isalpha():
                # unversioned entities for older versions will most likely land here.
                error = f"Incompatible bytes for {cls.__name__}."
            raise cls.InvalidHeader(error)
        return brand

    @classmethod
    def _parse_version(cls, data: bytes) -> int:
        version_data = data[cls._BRAND_LENGTH:cls._HEADER_SIZE]
        version_number = int.from_bytes(version_data, 'big')
        known_version = version_number in cls._deserializers()
        if not known_version:
            available_versions = ",".join(str(v) for v in cls._deserializers())
            error = f'Incorrect or unknown version "{version_number}". Available versions for {cls.__name__}: ({available_versions})'
            raise cls.InvalidHeader(error)
        return version_number

    @classmethod
    def _deserializers(cls) -> Dict[int, Callable]:
        """Return a dict of all known deserialization handlers for this class keyed by version"""
        return {cls._version(): cls._from_bytes_current, **cls._old_version_handlers()}


# Collects the brands of every serializable entity
SERIALIZABLE_ENTITIES = {v.__class__.__name__: v._brand() for v in Versioned.__subclasses__()}
