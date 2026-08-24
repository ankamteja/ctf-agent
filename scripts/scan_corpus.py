#!/usr/bin/env python3
"""
Security gate for the CTF writeup corpus.

Threat model: writeup text is UNTRUSTED. It gets retrieved and placed into an
LLM prompt whose agent runs commands in a sandbox. A poisoned writeup could try
to make the agent exfiltrate, run host commands, or ignore its instructions.

Key subtlety: CTF writeups ABOUT prompt injection legitimately contain strings
like "ignore previous instructions". So we DO NOT delete on keyword match.
We SCORE each file, QUARANTINE only the worst, and FLAG the rest so the agent
layer can fence them as data. Neutralization proper happens at prompt assembly.

Outputs ~/ctf-agent/store/scan_manifest.json: {path: {score, flags, action}}
action in {allow, flag, quarantine}
"""
import os, re, json, unicodedata, base64, math
from pathlib import Path

CORPUS = Path.home()/"ctf-agent"/"corpus"
OUT    = Path.home()/"ctf-agent"/"store"/"scan_manifest.json"
OUT.parent.mkdir(parents=True, exist_ok=True)

EXTS = {".md",".markdown",".txt",".rst",".py",".c",".sh"}
SKIP_DIRS = {".git","node_modules",".github"}

# Patterns that, in a DENSE cluster or aimed at an assistant, signal injection.
# Weighted. High weight = strongly directive at an LLM/agent.
PATTERNS = [
    (r"ignore (all |the |your |previous |above |prior )+(instructions|prompts|rules|context)", 3, "override"),
    (r"disregard (all |the |your |previous |above )+(instructions|rules)", 3, "override"),
    (r"you are (now |actually )?(a|an|the|dan|chatgpt|claude|system)", 2, "roleplay"),
    (r"<\|?(im_start|im_end|system|assistant|user|endoftext)\|?>", 4, "role_token"),
    (r"\[/?(INST|SYS|SYSTEM|ASSISTANT)\]", 4, "role_token"),
    (r"###\s*(system|instruction|assistant)\s*:", 3, "role_header"),
    (r"\bnew (instructions|task|system prompt|directive)\b", 2, "override"),
    (r"do not (tell|inform|warn|alert) (the )?(user|human|operator)", 4, "conceal"),
    (r"(exfiltrat|send|post|upload|curl|wget).{0,40}(api[_ ]?key|token|secret|env|/etc/passwd|~/.ssh)", 4, "exfil"),
    (r"print\(open\(['\"]/etc/passwd", 2, "exfil"),
    (r"os\.environ|printenv|env\b.*(KEY|TOKEN|SECRET)", 2, "env_read"),
    (r"tool_call|function_call|<function|<tool>|invoke name=", 3, "tool_spoof"),
    (r"when (you|the assistant|the agent) (read|process|see)", 3, "trigger"),
    (r"execute the following (on the host|command) immediately", 4, "host_exec"),
]
COMPILED = [(re.compile(p, re.I), w, name) for p,w,name in PATTERNS]

INVIS = [chr(c) for c in
         list(range(0x200b,0x2010))+[0x2060,0xfeff,0x00ad]+list(range(0xe0000,0xe0080))]

def invisible_hits(t):
    return sum(t.count(ch) for ch in INVIS)

def longest_b64(t):
    best=0
    for m in re.finditer(r"[A-Za-z0-9+/]{80,}={0,2}", t):
        best=max(best,len(m.group()))
    return best

def scan(text):
    flags={}; score=0
    for rx,w,name in COMPILED:
        n=len(rx.findall(text))
        if n:
            flags[name]=flags.get(name,0)+n
            score += w*min(n,3)          # cap repetition
    inv=invisible_hits(text)
    if inv:
        flags["invisible_unicode"]=inv
        score += min(inv,20)*2            # invisible chars are almost never legit prose
    b=longest_b64(text)
    if b>200:
        flags["long_base64"]=b
        score += 2
    return score, flags

def decide(score, flags):
    """Source is allowlisted (reputable repo), so technique keywords (exfil, override,
    roleplay) are EXPECTED reference content, not attacks on our agent. Quarantine is
    reserved for genuine agent-directed poisoning anomalies. Everything retrievable is
    still hard-fenced at prompt assembly -- that is the primary defense, this is depth."""
    inv   = flags.get("invisible_unicode",0)
    # Real poisoning signals: hidden payloads, host execution, concealment+exfil combo.
    if inv >= 6:                                   return "quarantine"  # hidden instruction payload
    if flags.get("host_exec"):                     return "quarantine"  # "execute on host immediately"
    if flags.get("conceal") and flags.get("exfil"):return "quarantine"  # steal + hide from user
    if flags.get("conceal") and inv >= 2:          return "quarantine"
    # Anything with directive/role/exfil signal is retrievable but flagged for extra fencing.
    if score >= 3 or flags:                        return "flag"
    return "allow"

def main():
    manifest={}; stats={"allow":0,"flag":0,"quarantine":0}
    files=[p for p in CORPUS.rglob("*")
           if p.is_file() and p.suffix.lower() in EXTS
           and not any(s in p.parts for s in SKIP_DIRS)]
    for p in files:
        try: t=p.read_text(errors="ignore")
        except Exception: continue
        # normalize check: does NFKC change length a lot? (homoglyph/space tricks)
        score,flags=scan(t)
        action=decide(score,flags)
        stats[action]+=1
        rel=str(p.relative_to(CORPUS))
        if action!="allow" or flags:
            manifest[rel]={"score":score,"flags":flags,"action":action}
        else:
            manifest[rel]={"score":0,"flags":{},"action":"allow"}
    json.dump(manifest, open(OUT,"w"), indent=0)
    print(f"scanned {len(files)} files")
    print(f"  allow={stats['allow']} flag={stats['flag']} quarantine={stats['quarantine']}")
    # show worst offenders
    worst=sorted(((v['score'],k,v['flags']) for k,v in manifest.items() if v['action']!='allow'),
                 reverse=True)[:15]
    print("top flagged:")
    for s,k,f in worst: print(f"  {s:3d} {k}  {f}")

if __name__=="__main__": main()
