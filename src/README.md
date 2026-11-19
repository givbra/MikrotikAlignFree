# Sources

`mikrotikalignfree.py` in the repository root is generated from these files:

```
cd src
python make.py          # writes src/mikrotikalignfree.py
```

- `base.py`, `ext.py` - server code (Bulgarian source strings, wrapped in `TR()` for English)
- `page.html` - the web interface (Bulgarian source text)
- `i18n/en.json` - English translations (`page`: interface text segments, `srv`: server messages)
- `leaflet.js`, `leaflet.css` - Leaflet 1.9.4 (BSD-2-Clause), embedded into the single file
