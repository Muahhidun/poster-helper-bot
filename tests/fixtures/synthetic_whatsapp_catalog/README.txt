Synthetic local regression input only.
These five minimal rows were constructed from IDs/names already asserted by
tests/test_whatsapp_queue.py. They were not exported from any Poster account,
production database, user catalog or WhatsApp chat. Account labels are fixture
labels used by those existing tests, not credentials or live connections.
run_furniture_tests.py copies them only into its own TemporaryDirectory when
--synthetic-whatsapp-catalog is explicitly selected. It never overwrites a
user catalog. External networking remains disabled throughout the tests.
