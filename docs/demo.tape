# Terminal demo for the README. Render with: vhs docs/demo.tape
Output docs/demo.gif

Set Shell "bash"
Set FontSize 16
Set Width 1100
Set Height 620
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 18ms

Hide
Type "export PS1='$ ' OBSIDIYAN_HOME=/tmp/obsidiyan-demo && rm -rf $OBSIDIYAN_HOME && alias obsidiyan='.venv/bin/python -m obsidiyan' && clear"
Enter
Show

Type "obsidiyan init /tmp/obsidiyan-demo --demo"
Sleep 150ms
Enter
Sleep 1.2s

Type "obsidiyan search postgres backups      # all terms must match"
Sleep 150ms
Enter
Sleep 1.4s

Type "obsidiyan search Acme              # confidential, hidden by default"
Sleep 150ms
Enter
Sleep 1.4s

Type "obsidiyan search Acme --include-nda"
Sleep 150ms
Enter
Sleep 1.4s

Type "clear && .venv/bin/python scripts/verify-nda.py && .venv/bin/python scripts/verify-mcp.py"
Sleep 150ms
Enter
Sleep 3.5s
