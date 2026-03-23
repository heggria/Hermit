import pathlib

fp = pathlib.Path("src/hermit/plugins/builtin/hooks/quality/council_arbiter.py")
lines = fp.read_text().splitlines(keepends=True)

# Find the line with max_revision_cycles default
for i, line in enumerate(lines):
    if "max_revision_cycles: int = 3," in line:
        indent = "        "
        comment = (
            f"{indent}# 3 cycles: (1) initial review, (2) targeted re-review of\n"
            f"{indent}# revised code, (3) final verification pass.\n"
        )
        lines.insert(i, comment)
        break

fp.write_text("".join(lines))
print("Done")
