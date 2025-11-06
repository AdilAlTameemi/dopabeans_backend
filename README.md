# Website Backend

The FastAPI backend now requires a Supabase Postgres connection string. Set `DATABASE_URL`
to the Supabase URI (for example the pooled connection string) before starting the server.
All persistence (orders, categories, products, modifiers, etc.) is read from and written
to that database so it remains the single source of truth for the website and the menus.

## Schema migrations at startup

By default the API skips the long-running Postgres migration block that used to execute on
every boot (it rewrites Foodics tables, drops/creates constraints, etc.). This keeps Render
deployments fast. If you intentionally need to run those migrations — for example after
changing the table schema — set the environment variable `RUN_SCHEMA_MIGRATIONS=true`
for one deploy, let it finish, and then turn it back off.
