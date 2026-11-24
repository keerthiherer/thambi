from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate sherpa-onnx KWS tokens from plain keywords")
    ap.add_argument("--tokens", required=True)
    ap.add_argument("--bpe", help="BPE model for token-based models")
    ap.add_argument("--lexicon", help="CMU-style pronunciation lexicon for phone-based models")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    if not args.bpe and not args.lexicon:
        ap.error("one of --bpe or --lexicon is required")

    import sherpa_onnx

    texts: list[str] = []
    extras: list[list[str]] = []
    for raw in Path(args.input).read_text(encoding="utf-8").splitlines():
        parts = raw.strip().split()
        if not parts:
            continue
        words, extra = [], []
        for part in parts:
            if part[0] in (":", "#", "@"):
                extra.append(part)
            else:
                words.append(part)
        if words:
            texts.append(" ".join(words))
            extras.append(extra)

    kwargs = {}
    if args.lexicon:
        tokens_type = "phone+ppinyin"
        kwargs["lexicon"] = args.lexicon
    else:
        tokens_type = "bpe"
        kwargs["bpe_model"] = args.bpe
    encoded = sherpa_onnx.text2token(
        texts=texts,
        tokens=args.tokens,
        tokens_type=tokens_type,
        **kwargs,
    )
    lines = []
    for tokens, extra in zip(encoded, extras):
        lines.append(" ".join(list(tokens) + extra))
    Path(args.output).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(lines)} wake phrase(s) to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
