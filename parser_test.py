#! /usr/bin/env python
import parsley


def main():
    grammar = """\
    line = msg_number:m ws plist:x end -> (m, x)
    msg_number = digit+:dl -> int("".join(dl))
    plist = '(' items:x ')' -> x
    items = item_space*:x -> x
    item_space = item:x ws -> x
    item = string_item | plist
    string_item = atom | qstring
    atom = (letterOrDigit | '-')+:c -> ''.join(c)
    qstring = '"' qstring_contents:a '"' -> a
    qstring_contents = qstring_chars+:c -> ''.join(c)
    qstring_chars = anything:c ?(c not in '"') -> c
    """
    parser = parsley.makeGrammar(grammar, {})
    text = (
        r"""22 (X-GM-THRID 1807293799267142312 X-GM-MSGID """
        r"""1807374185704953928 X-GM-LABELS ("Starred" Thanks) UID 81642)"""
    )
    text = (
        """22 (X-GM-THRID 1807293799267142312 """
        """X-GM-MSGID 1807374185704953928 X-GM-LABELS ("\\Starred" Thanks) UID 81642)"""
    )
    print(f"TEXT: {text}")
    print("")
    print(parser(text).line())


if __name__ == "__main__":
    main()
