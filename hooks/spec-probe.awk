function advance(    char, start, hex, code) {
  while (position <= size && substr(input, position, 1) ~ /[ \t\r\n]/) position++
  token = substr(input, position, 1)
  position++
  escaped = 0
  value = ""
  if (token == "\"") {
    while (position <= size) {
      char = substr(input, position++, 1)
      if (char == "\"") { token = "string"; return }
      if (char == "\\") {
        escaped = 1
        code = substr(input, position++, 1)
        if (code == "u") {
          hex = substr(input, position, 4)
          if (hex !~ /^[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]$/) bad = 1
          position += 4
        } else if (index("\"\\/bfnrt", code) == 0) bad = 1
      } else {
        if (char ~ /[[:cntrl:]]/) bad = 1
        value = value char
      }
    }
    bad = 1
    return
  }
  if (token == "" || index("{}[],: ", token)) return
  start = position - 1
  while (position <= size && index(" \t\r\n[]{},:", substr(input, position, 1)) == 0) position++
  value = substr(input, start, position - start)
  if (value !~ /^(true|false|null|-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?)$/) bad = 1
  token = "scalar"
}

function parse_value(depth,    key, key_escape, target) {
  if (depth > 128) { bad = 1; return }
  if (token == "{") {
    advance()
    if (token == "}") { advance(); return }
    while (!bad) {
      if (token != "string") { bad = 1; return }
      key = value
      key_escape = escaped
      if (depth == 1 && key_escape) ambiguous = 1
      advance()
      if (token != ":") { bad = 1; return }
      advance()
      target = depth == 1 && (key == "cwd" || key == "workspaceRoot" || key == "workingDirectory")
      if (target) {
        if (token != "string" || escaped) ambiguous = 1
        else paths[key] = value
      }
      parse_value(depth + 1)
      if (token == "}") { advance(); return }
      if (token != ",") { bad = 1; return }
      advance()
    }
  } else if (token == "[") {
    advance()
    if (token == "]") { advance(); return }
    while (!bad) {
      parse_value(depth + 1)
      if (token == "]") { advance(); return }
      if (token != ",") { bad = 1; return }
      advance()
    }
  } else if (token == "string" || token == "scalar") advance()
  else bad = 1
}

{ input = input (NR > 1 ? "\n" : "") $0 }
END {
  size = length(input)
  position = 1
  advance()
  if (token != "{") bad = 1
  else parse_value(1)
  if (bad || ambiguous || token != "") print "!"
  else print "C" (paths["cwd"] ? paths["cwd"] : paths["workspaceRoot"] ? paths["workspaceRoot"] : paths["workingDirectory"])
}
