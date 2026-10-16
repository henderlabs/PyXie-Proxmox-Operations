from pyxie_core.iso_upload import build_multipart


def test_multipart_framing():
    ctype, pre, post, total = build_multipart("a.iso", 1000)
    b = ctype.split("boundary=")[1]
    assert ctype.startswith("multipart/form-data; boundary=pyxie")
    assert pre.startswith(f"--{b}\r\n".encode()) and b'name="content"\r\n\r\niso\r\n' in pre
    assert pre.endswith(b'Content-Type: application/octet-stream\r\n\r\n') and b'name="filename"; filename="a.iso"' in pre   # the file part is last
    assert post == f"\r\n--{b}--\r\n".encode() and total == len(pre) + 1000 + len(post)


def test_multipart_checksum_fields_come_before_the_file():
    _, pre, _, _ = build_multipart("a.iso", 5, checksum="ab" * 32, algorithm="sha256")
    text = pre.decode()
    assert text.index('name="checksum-algorithm"') < text.index('name="checksum"') < text.index('name="filename"')
