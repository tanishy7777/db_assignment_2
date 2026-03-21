import os
from bplustree import BPlusTree


def run_demo():
    bpt = BPlusTree(order=4)

    os.makedirs("../images", exist_ok=True)

    insert_keys = [10, 20, 5, 6, 12, 30, 7, 17, 3, 25, 15]

    for key in insert_keys:
        bpt.insert(key, f"val{key}")
        print(f"Inserted {key}")

    insert_path = "../images/tree_after_inserts"
    bpt.visualize_tree(insert_path)
    print(f"Saved: {insert_path}.png")

    delete_keys = [6, 7, 5, 10, 12]
    for i, key in enumerate(delete_keys):
        bpt.delete(key)
        print(f"Deleted {key}")

        path = f"../images/tree_delete_step_{i+1}"
        bpt.visualize_tree(path)
        print(f"Saved: {path}.png")


if __name__ == "__main__":
    run_demo()