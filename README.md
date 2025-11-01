# Website Backend

The FastAPI backend now requires a Supabase Postgres connection string. Set `DATABASE_URL`
to the Supabase URI (for example the pooled connection string) before starting the server.
All persistence (orders, categories, products, modifiers, etc.) is read from and written
to that database so it remains the single source of truth for the website and the menus.
