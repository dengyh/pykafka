"""
Author: Keith Bourgoin, Emmett Butler
"""
__license__ = """
Copyright 2015 Parse.ly, Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
__all__ = ["unpack_from"]
import itertools
import re
import struct
from .compat import range


def unpack_from(fmt, buff, offset=0):
    """A customized version of `struct.unpack_from`

    This is a conveinence function that makes decoding the arrays,
    strings, and byte arrays that we get from Kafka significantly
    easier. It takes the same arguments as `struct.unpack_from` but
    adds 3 new formats:

    * Wrap a section in `[]` to indicate an array. e.g.: `[ii]`
    * `S` for strings (int16 followed by byte array)
    * `Y` for byte arrays (int32 followed by byte array)

    Spaces are ignored in the format string, allowing more readable formats

    NOTE: This may be a performance bottleneck. We're avoiding a lot of memory
          allocations by using the same buffer, but if we could call
          `struct.unpack_from` only once, that's about an order of magnitude
          faster. However, constructing the format string to do so would erase
          any gains we got from having the single call.
    """
    fmt = fmt.replace(' ', '')
    if fmt[0] in '!><':
        fmt = fmt[1:]  # It's always network ordering

    output = _unpack(fmt, buff, offset, 1)[0]

    # whole-message arrays come back weird
    if fmt[0] == '[' and len(output) == 1:
        output = output[0]

    return output


def _unpack(fmt, buff, offset, count=1):
    """Recursive call for unpacking

    :param fmt: The struct format string
    :type fmt: str
    :param buff: The buffer into which to unpack
    :type buff: buffer
    :param offset: The offset at which to start unpacking
    :type offset: int
    :param count: The number of items in the array
    :type count: int
    """
    items = []
    array_fmt = None
    for i, ch in enumerate(fmt):
        if array_fmt is not None:
            if ch == ']':
                if array_fmt.count('[') == array_fmt.count(']'):
                    # array format done, call _unpack for this format string
                    count = struct.unpack_from('!i', buff, offset)[0]
                    array_item, offset = _unpack_array(array_fmt, buff,
                                                       offset + 4, count)
                    items.append(array_item)
                    array_fmt = None
                    continue  # done with this
            # not done yet, append to ongoing format
            array_fmt += ch
        elif ch == '[':
            array_fmt = ''  # starts building string for array unpack
        elif ch == 'V':
            len_, unpacked = unpack_varint_from(buff, offset)
            items.append(unpacked)
            offset += len_
        else:
            if ch in 'SY':
                len_fmt = '!h' if ch == 'S' else '!i'
                len_ = struct.unpack_from(len_fmt, buff, offset)[0]
                offset += struct.calcsize(len_fmt)
                if len_ == -1:
                    items.append(None)
                    continue
                ch = '%ds' % len_
            items.extend(struct.unpack_from('!' + ch, buff, offset))
            offset += struct.calcsize(ch)
    return tuple(items), offset


def _unpack_array(fmt, buff, offset, count):
    """Unpack an array of items.

    :param fmt: The struct format string
    :type fmt: str
    :param buff: The buffer into which to unpack
    :type buff: buffer
    :param offset: The offset at which to start unpacking
    :type offset: int
    :param count: The number of items in the array
    :type count: int
    """
    output = []
    for i in range(count):
        item, offset = _unpack(fmt, buff, offset)
        output.append(item)
    if len(fmt) == 1:
        output = list(itertools.chain.from_iterable(output))
    return output, offset


def unpack_varint_from(buff, offset):
    size = 0
    shift = 0
    result = 0
    while True:
        size += 1
        i = ord(buff[offset:offset + 1])
        offset += 1
        result |= (i & 0x7f) << shift
        shift += 7
        if not (i & 0x80):
            break
    return size, result


NOARG_STRUCT_FMTS = re.compile(r'[^xcbB\?hHiIlLqQfdspP]')


def pack_into(fmt, buff, offset, *args):
    if 'V' in fmt:
        size = 0
        args = list(args)
        parts = [p for p in re.split('(V)', fmt) if p]
        for i, fmt_part in enumerate(parts):
            if fmt_part != "V":
                args_only_fmt = re.sub(NOARG_STRUCT_FMTS, '', fmt_part)
                part_args = [args.pop(0) for _ in range(len(args_only_fmt))]
                prefixed = "!" + fmt_part if fmt.startswith("!") and i != 0 else fmt_part
                struct.pack_into(prefixed, buff, offset, *part_args)
                fmtsize = struct.calcsize(prefixed)
                offset += fmtsize
                size += fmtsize
            else:
                fmtsize = pack_varint_into(buff, offset, args.pop(0))
                offset += fmtsize
                size += fmtsize
        return size
    else:
        return struct.pack_into(fmt, buff, offset, *args)


def pack_varint_into(buff, offset, val):
    size = 0
    while True:
        towrite = val & 0x7f
        val >>= 7
        size += 1
        if val:
            struct.pack_into('c', buff, offset, chr(towrite | 0x80))
            offset += 1
        else:
            struct.pack_into('c', buff, offset, chr(towrite))
            offset += 1
            break
    return size
