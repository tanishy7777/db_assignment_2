import math
from bisect import bisect_left, bisect_right
from graphviz import Digraph


class BPlusTreeNode:
    __slots__ = 'is_leaf', 'keys', 'children', 'values', 'next'

    def __init__(self, is_leaf=False):
        self.is_leaf = is_leaf
        self.keys = []
        self.children = []
        self.values = []
        self.next = None


class BPlusTree:
    def __init__(self, order=4):
        self.root = BPlusTreeNode(is_leaf=True)
        self.order = order

    def search(self, key):
        curr = self.root
        while not curr.is_leaf:
            i = bisect_right(curr.keys, key)
            curr = curr.children[i]

        i = bisect_left(curr.keys, key)
        if i < len(curr.keys) and curr.keys[i] == key:
            return curr.values[i]
        return None

    def insert(self, key, value):
        root = self.root
        if len(root.keys) == self.order - 1:
            new_root = BPlusTreeNode(is_leaf=False)
            new_root.children.append(self.root)
            self._split_child(new_root, 0)
            self.root = new_root
            self._insert_non_full(self.root, key, value)
        else:
            self._insert_non_full(self.root, key, value)

    def _insert_non_full(self, node, key, value):
        if node.is_leaf:
            i = bisect_right(node.keys, key)
            node.keys.insert(i, key)
            node.values.insert(i, value)
        else:
            i = bisect_right(node.keys, key)
            if len(node.children[i].keys) == self.order - 1:
                self._split_child(node, i)
                if key > node.keys[i]:
                    i += 1
            self._insert_non_full(node.children[i], key, value)

    def _split_child(self, parent, index):
        order = self.order
        child = parent.children[index]
        new_node = BPlusTreeNode(is_leaf=child.is_leaf)

        mid = math.ceil(order / 2) - 1

        if child.is_leaf:
            new_node.keys = child.keys[mid:]
            new_node.values = child.values[mid:]
            child.keys = child.keys[:mid]
            child.values = child.values[:mid]

            new_node.next = child.next
            child.next = new_node

            parent.keys.insert(index, new_node.keys[0])
            parent.children.insert(index + 1, new_node)
        else:
            new_node.keys = child.keys[mid + 1:]
            new_node.children = child.children[mid + 1:]
            up_key = child.keys[mid]

            child.keys = child.keys[:mid]
            child.children = child.children[:mid + 1]

            parent.keys.insert(index, up_key)
            parent.children.insert(index + 1, new_node)

    def range_query(self, start_key, end_key):
        curr = self.root
        while not curr.is_leaf:
            i = bisect_right(curr.keys, start_key)
            curr = curr.children[i]

        results = []
        while curr is not None:
            i = bisect_left(curr.keys, start_key)
            while i < len(curr.keys):
                k = curr.keys[i]
                if k <= end_key:
                    results.append((k, curr.values[i]))
                else:
                    return results
                i += 1
            curr = curr.next
        return results

    def delete(self, key):
        curr = self.root
        while not curr.is_leaf:
            i = bisect_right(curr.keys, key)
            curr = curr.children[i]

        i = bisect_left(curr.keys, key)
        if i < len(curr.keys) and curr.keys[i] == key:
            curr.keys.pop(i)
            curr.values.pop(i)
            return True
        return False

    def visualize(self, filename="bplustree"):
        dot = Digraph()
        dot.attr(rankdir="TB")

        node_map = {}
        node_id = 0

        def traverse(node):
            nonlocal node_id

            curr_id = f"node{node_id}"
            node_map[node] = curr_id
            node_id += 1

            if node.is_leaf:
                rows = ""
                for k, v in zip(node.keys, node.values):
                    rows += f"<TR><TD>{k}</TD><TD>{v}</TD></TR>"

                label = f"""<
                <TABLE BORDER="1" CELLBORDER="1" CELLSPACING="0">
                    {rows}
                </TABLE>
                >"""

                dot.node(curr_id, label=label)

            else:
                label = " | ".join(str(k) for k in node.keys)
                dot.node(curr_id, label)

            if not node.is_leaf:
                for child in node.children:
                    child_id = traverse(child)
                    dot.edge(curr_id, child_id)

            return curr_id

        traverse(self.root)

        curr = self.root
        while not curr.is_leaf:
            curr = curr.children[0]

        while curr and curr.next:
            dot.edge(
                node_map[curr],
                node_map[curr.next],
                style="dashed",
                constraint="false"
            )
            curr = curr.next

        dot.render(filename, format="png", cleanup=True)