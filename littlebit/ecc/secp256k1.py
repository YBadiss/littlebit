import hmac
from dataclasses import dataclass, field
from hashlib import sha256
from typing import cast

from .field import FieldElement
from .point import Point

# secp256k1 elliptic curve equation: y² = x³ + 7

# Elliptic curve parameters A and B of the curve : y² = x³ Ax + B
A: int = 0
B: int = 7

# Prime of the finite field
P: int = 2 ** 256 - 2 ** 32 - 2 ** 9 - 2 ** 8 - 2 ** 7 - 2 ** 6 - 2 ** 4 - 1


class S256FieldElement(FieldElement):
    def __init__(self, number, prime=None):
        super().__init__(number=number, prime=P)

    def __repr__(self):
        return f"{self.number:x}".zfill(64)

    def sqrt(self):
        ###############################################################################
        # Lemma 1: the prime p used in secp256k1 is such that p % 4 == 3              #
        # Lemma 2: nᵖ⁻¹ % p == 1 (Fermat's Little Theorem)                            #
        ###############################################################################

        # Let's denote self as w² for brevity. We want to find w.
        #
        #    w² = w² x 1
        #       = w² x wᵖ⁻¹                                         [... using Lemma 2]
        #       = wᵖ⁺¹
        # => w  = w⁽ᵖ⁺¹⁾ᐟ²
        #       = w²⁽ᵖ⁺¹⁾ᐟ⁴
        #       = (w²)⁽ᵖ⁺¹⁾ᐟ⁴
        #
        # The power (p+1)/4 is an integer, since p % 4 == 3         [... using Lemma 1]
        #
        # Hence, if w² = v and p % 4 == 3, then w = v⁽ᵖ⁺¹⁾ᐟ⁴

        return self ** ((self.prime + 1) // 4)


@dataclass
class Signature:
    r: int
    s: int

    def __repr__(self):
        return f"Signature(r={self.r:x}, s={self.s:x})"

    def der(self) -> bytes:
        # 1. Start with the 0x30 byte.
        # 2. Append the length of the rest of the signature.
        # 3. Append the marker byte, 0x02.
        # 4. Encode r as a big-endian integer, but prepend it with the 0x00 byte if r's
        #    first byte >= 0x80. Prepend the resulting length to r. Add this to the
        #    result.
        # 5. Append the marker byte, 0x02.
        # 6. Encode s as a big-endian integer, but prepend it with the 0x00 byte if s's
        #    first byte >= 0x80. Prepend the resulting length to s. Add this to the
        #    result.

        PREFIX = 0x30
        MARKER = 0x02
        NULL_BYTE = b"\x00"

        # Serialze 256-bit integer to bytes with big-endian byteorder, and strip leading
        # null bytes
        rbin = self.r.to_bytes(32, byteorder="big").lstrip(NULL_BYTE)

        # We check if rbin is negative, by verifying if the first bit of rbin is 1.
        # This is brought about by doing a bitwise AND between the first bit of rbin
        # and (10000000)₂ or (0x80)₁₆.
        if rbin[0] & 0x80:
            rbin = NULL_BYTE + rbin

        result = bytes([MARKER, len(rbin)]) + rbin

        # Serialze 256-bit integer to bytes with big-endian byteorder, and strip leading
        # null bytes
        sbin = self.s.to_bytes(32, byteorder="big").lstrip(NULL_BYTE)

        # We check if sbin is negative, by verifying if the first bit of sbin is 1.
        # This is brought about by doing a bitwise AND between the first bit of sbin
        # and (10000000)₂ or (0x80)₁₆.
        if sbin[0] & 0x80:
            sbin = NULL_BYTE + sbin
        result += bytes([MARKER, len(sbin)]) + sbin

        return bytes([PREFIX, len(result)]) + result


class S256Point(Point):
    def __init__(self, x, y, a=None, b=None):
        a, b = S256FieldElement(A), S256FieldElement(B)

        if isinstance(x, int) and isinstance(y, int):
            super().__init__(x=S256FieldElement(x), y=S256FieldElement(y), a=a, b=b)
        else:
            # x and y are either both None, or both instances of S256FieldElement
            super().__init__(x=x, y=y, a=a, b=b)

    def __rmul__(self, coefficient):
        return super().__rmul__(coefficient % N)

    def verify(self, z: int, signature: Signature) -> bool:
        s_inv = pow(signature.s, N - 2, N)
        u = (z * s_inv) % N
        v = (signature.r * s_inv) % N

        total = u * G + v * self
        return total.x.number == signature.r

    def sec(self, compressed: bool = True) -> bytes:
        """
        Returns binary version of the Standards for Efficient Cryptography (SEC)
        format.

        Uncompressed SEC format:
            1. Start with the prefix byte, 0x04
            2. Append the x coordinate in 32 bytes as a big-endian integer
            3. Append the y coordinate in 32 bytes as a big-endian integer

        Compressed SEC format:
            1. Start with the prefix byte.
               If y is even, it's 0x02; otherwise, it's 0x03
            2. Append the x coordinate in 32 bytes as a big-endian integer

        All 256 bit integers are encoded in 32 bytes, big-endian.
        """
        if compressed:
            if self.y.number % 2 == 0:
                return b"\x02" + self.x.number.to_bytes(32, "big")
            else:
                return b"\x03" + self.x.number.to_bytes(32, "big")
        else:
            return (
                b"\x04"
                + self.x.number.to_bytes(32, "big")
                + self.y.number.to_bytes(32, "big")
            )

    @classmethod
    def parse(cls, sec_bin: bytes) -> "S256Point":
        # Handle the case of uncompressed SEC format
        if sec_bin[0] == 4:  # check if prefix byte is b'\x04'
            return cls(
                x=int.from_bytes(sec_bin[1:33], "big"),
                y=int.from_bytes(sec_bin[33:65], "big"),
            )

        is_y_expected_to_be_even = sec_bin[0] == 2  # prefix byte is b'\x02'
        x = S256FieldElement(number=int.from_bytes(sec_bin[1:], "big"))

        # Right side of the equation: y² = x³ + 7
        alpha = x ** 3 + S256FieldElement(number=B)

        # [FIXME] - Dirty hack to indicate mypy about the true type
        alpha = cast(S256FieldElement, alpha)

        y = alpha.sqrt()

        if is_y_expected_to_be_even:
            if y.number % 2 == 0:
                return cls(x=x, y=y)
            else:
                return cls(x=x, y=S256FieldElement(P - y.number))
        else:
            if y.number % 2 == 0:
                return cls(x=x, y=S256FieldElement(P - y.number))
            else:
                return cls(x=x, y=y)


# Generator point
G = S256Point(
    x=0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    y=0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)

# Order of the Group generated by G, such that nG = I
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


@dataclass
class PrivateKey:
    secret: int
    point: int = field(init=False)  # public key

    def __post_init__(self):
        self.point = self.secret * G

    @property
    def hex(self) -> str:
        return f"{self.secret:x}".zfill(64)

    def sign(self, z: int) -> Signature:
        k = self.deterministic_k(z)  # or, randint(0, N)
        R = k * G
        r = R.x.number
        k_inv = pow(k, N - 2, N)
        s = ((z + r * self.secret) * k_inv) % N
        if s > N / 2:
            s = N - s
        return Signature(r, s)

    def deterministic_k(self, z: int) -> int:
        """
        Directly based on RFC 6979:
            Deterministic Usage of the Digital Signature Algorithm (DSA) and
            Elliptic Curve Digital Signature Algorithm (ECDSA)

        All 256 bit integers are encoded in 32 bytes, big-endian.

        [TODO] - Explain me
        """
        k = b"\x00" * 32
        v = b"\x01" * 32
        if z > N:
            z -= N
        z_bytes = z.to_bytes(32, "big")
        secret_bytes = self.secret.to_bytes(32, "big")
        k = hmac.new(k, v + b"\x00" + secret_bytes + z_bytes, sha256).digest()
        v = hmac.new(k, v, sha256).digest()
        k = hmac.new(k, v + b"\x01" + secret_bytes + z_bytes, sha256).digest()
        v = hmac.new(k, v, sha256).digest()
        while True:
            v = hmac.new(k, v, sha256).digest()
            candidate = int.from_bytes(v, "big")
            if 1 <= candidate < N:
                return candidate
            k = hmac.new(k, v + b"\x00", sha256).digest()
            v = hmac.new(k, v, sha256).digest()
