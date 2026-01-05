from autohub.database.connection import engine, Base
import autohub.database.model as models
from sqlalchemy import inspect

print('registered_tables:', sorted(list(Base.metadata.tables.keys())))
ins = inspect(engine)
print('db_tables:', sorted(ins.get_table_names()))
