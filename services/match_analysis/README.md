# Crickium Match Analysis

This folder owns the HTML match-analysis system.

- `collector.py` converts the existing engine state into a normalized report model and computes report-only analytics.
- `renderer.py` creates one self-contained HTML document.
- `sender.py` allocates the global report number, builds the Telegram caption and sends the document.
- `template.html` is the document shell.
- `theme.css` is the Crickium-specific visual theme.
- `generated/` is a small local working cache for the most recent generated reports and is pruned automatically.

The report layer is telemetry-only. It does not participate in the scoring resolver and should never be used as an input to match simulation.
