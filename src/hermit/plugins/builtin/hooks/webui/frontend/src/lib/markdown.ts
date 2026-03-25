/**
 * Strip custom XML-like tags (e.g. <highlight>, <note>, <thinking>) from LLM
 * output so react-markdown renders clean prose. Tags are replaced with
 * markdown-native equivalents where sensible.
 */

const TAG_REPLACEMENTS: Record<string, { open: string; close: string }> = {
  highlight: { open: "> **", close: "**" },
  note: { open: "> ", close: "" },
  thinking: { open: "", close: "" },
  reflection: { open: "", close: "" },
};

const KNOWN_TAGS = Object.keys(TAG_REPLACEMENTS).join("|");

// Matches <tag>...</tag> (single-line and multi-line)
const TAG_RE = new RegExp(
  `<(${KNOWN_TAGS})>([\\s\\S]*?)<\\/\\1>`,
  "gi",
);

// Matches orphaned opening or closing tags that weren't paired
const ORPHAN_RE = new RegExp(`<\\/?(${KNOWN_TAGS})>`, "gi");

export function cleanMarkdown(raw: string): string {
  let result = raw.replace(TAG_RE, (_match, tag: string, content: string) => {
    const key = tag.toLowerCase();
    const rep = TAG_REPLACEMENTS[key];
    if (!rep) return content.trim();
    const trimmed = content.trim();
    if (!trimmed) return "";
    // For blockquote-style replacements, prefix each line with >
    if (rep.open.startsWith("> ")) {
      const lines = trimmed.split("\n");
      const prefixed = lines.map((l) => `> ${l}`).join("\n");
      if (rep.open === "> **" && rep.close === "**") {
        // highlight: wrap first line as bold blockquote
        return `\n${prefixed}\n`;
      }
      return `\n${prefixed}\n`;
    }
    return rep.open + trimmed + rep.close;
  });

  // Remove any remaining orphaned tags
  result = result.replace(ORPHAN_RE, "");

  return result;
}
