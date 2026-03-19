from bplustree import BPlusTree

bptree = BPlusTree(order=4)

keys = [
    15, 3, 7, 20, 25, 1, 5, 30, 10, 12,
    8, 6, 18, 22, 27, 35, 40, 50, 2, 4,
    9, 11, 13, 14, 16, 17, 19, 21, 23, 24
]

for k in keys:
    bptree.insert(k, f"val_{k}")

bptree.visualize("tree_output")