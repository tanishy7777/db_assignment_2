from table import Table


class DatabaseManager:
    def __init__(self):
        self.tables = {}

    def create_table(self, table_name, columns, primary_key_index=0):
        if table_name in self.tables:
            raise ValueError(f"Table '{table_name}' already exists.")

        new_table = Table(table_name, columns, primary_key_index)
        self.tables[table_name] = new_table
        return new_table

    def get_table(self, table_name):
        if table_name not in self.tables:
            raise ValueError(f"Table '{table_name}' does not exist.")
        return self.tables[table_name]

    def drop_table(self, table_name):
        if table_name in self.tables:
            del self.tables[table_name]
            return True
        return False
