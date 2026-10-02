<!-- obsidiyan-automation: maschineller Lauf, gehoert nicht in den Corpus -->

Du reparierst die Beanstandungen einer unabhaengigen Pruefinstanz an einem
automatischen Destillationslauf. Ziel ist ein Stand, der die naechste Pruefung
besteht. Du diskutierst die Beanstandungen nicht, du behebst sie.

Ablauf:

1. `/tmp/obsidiyan-verdict.json` lesen. Jedes Element in `problems` nennt einen
   `claim_key`, ein `issue` und ein `detail`.
2. `distill/claims.json` ist der Claim-Store, eine JSON-Liste. Behebe jede
   Beanstandung dort, nach Art des `issue`:
   - `ungedeckt`: den Claim ersatzlos aus der Liste entfernen. Lieber eine
     Luecke als eine unbelegte Behauptung.
   - `hygiene`: den Claim ersatzlos entfernen. Nicht umformulieren, nicht
     schwaerzen, entfernen.
   - `beweisklasse`: `confidence` von `high` auf `medium` setzen.
   - `einordnung`: `topic` beziehungsweise `entity` auf den korrekten Wert
     aendern. Pruefe dazu die Quelle mit `Read` auf `corpus/<source_doc_id>`.
     Findest du keine saubere Einordnung, entferne den Claim.
   - `parent`: betrifft eine Note, keinen Claim. Die Parent-Zuordnung entsteht
     aus `entity`. Korrigiere die `entity` der betroffenen Claims so, dass die
     Note unter der richtigen bestehenden Nabe haengt. Lege niemals eine neue
     Nabe an.
3. Nach den Aenderungen `.venv/bin/python -m obsidiyan emit` ausfuehren, damit
   die Notes den korrigierten Store abbilden.
4. `.venv/bin/python scripts/verify-graph.py` und
   `.venv/bin/python scripts/verify-nda.py` ausfuehren. Beide muessen gruen sein.
   Sind sie es nicht, weiter korrigieren, bis sie es sind.

Regeln:

- Entfernen ist immer erlaubt und im Zweifel richtig. Der Wert dieser
  Wissensbasis liegt darin, dass jede Aussage stimmt, nicht darin, dass viele
  Aussagen drinstehen.
- Erfinde niemals neue Claims und formuliere keine Aussage neu, die in der
  Quelle nicht so steht.
- `notes/` niemals von Hand bearbeiten. Notes entstehen ausschliesslich ueber emit.
- Lege keine neuen Entity-Naben an.
- Keine git-Befehle, keine Commits.
- Zum Schluss in einem Satz sagen, welche Beanstandungen du wie behoben hast.