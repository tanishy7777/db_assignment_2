from bplustree import BPlusTree


class Table:
    def __init__(self, name, columns, primary_key_index=0):
        self.name = name
        self.columns = columns
        self.primary_key_index = primary_key_index
        self.index = BPlusTree(order=4)

    def insert_record(self, record):
        if len(record) != len(self.columns):
            raise ValueError("Record does not match table columns.")

        key = record[self.primary_key_index]
        self.index.insert(key, record)

    def search_record(self, key):
        return self.index.search(key)

    def delete_record(self, key):
        return self.index.delete(key)

    def range_query_records(self, start_key, end_key):
        return self.index.range_query(start_key, end_key)
