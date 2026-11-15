"""CreamFlow, the local Control Panel (SPEC §9b; step 14).

A React single-page app (sources in `ui/web/`, built per machine by `nqbot ui build` into the git-ignored
`ui/web/dist/`) served with a JSON API and server-sent events by a small Starlette + uvicorn server bound to
127.0.0.1:`ui_port` (`nqbot ui`). Kill and flat never depend on the React app: the no-JavaScript `/emergency` page is
rendered here from `templates/`, and it posts to the same action path (`actions.py`).
"""
