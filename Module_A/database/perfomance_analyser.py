import time
import tracemalloc
import random
import gc
import matplotlib.pyplot as plt
from bplustree import BPlusTree
from bruteforce import BruteForceDB


class PerformanceAnalyzer:
    def measure_time(self, num_keys):
        bptree = BPlusTree(order=4)
        brutedb = BruteForceDB()

        keys = random.sample(range(1, num_keys * 10), num_keys)
        search_keys = random.sample(keys, min(500, num_keys))
        range_start, range_end = min(keys), min(keys) + 5000

        results = {"B+ Tree": {}, "BruteForce": {}}

        start = time.perf_counter()
        for k in keys:
            bptree.insert(k, f"val_{k}")
        results["B+ Tree"]["insert"] = time.perf_counter() - start

        start = time.perf_counter()
        for k in search_keys:
            bptree.search(k)
        results["B+ Tree"]["search"] = time.perf_counter() - start

        start = time.perf_counter()
        bptree.range_query(range_start, range_end)
        results["B+ Tree"]["range"] = time.perf_counter() - start

        start = time.perf_counter()
        for k in keys:
            brutedb.insert(k)
        results["BruteForce"]["insert"] = time.perf_counter() - start

        start = time.perf_counter()
        for k in search_keys:
            brutedb.search(k)
        results["BruteForce"]["search"] = time.perf_counter() - start

        start = time.perf_counter()
        brutedb.range_query(range_start, range_end)
        results["BruteForce"]["range"] = time.perf_counter() - start

        return results, keys

    def measure_memory(self, keys):
        results = {"B+ Tree": {}, "BruteForce": {}}

        gc.collect()
        tracemalloc.start()
        bptree = BPlusTree(order=4)
        for k in keys:
            bptree.insert(k, f"val_{k}")
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        results["B+ Tree"]["memory"] = peak / (1024 * 1024)

        del bptree

        gc.collect()
        tracemalloc.start()
        brutedb = BruteForceDB()
        for k in keys:
            brutedb.insert(k)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        results["BruteForce"]["memory"] = peak / (1024 * 1024)

        del brutedb

        return results

    def run_scaling_benchmarks(self, key_sizes):
        all_stats = {
            "B+ Tree": {"insert": [], "search": [], "range": [], "memory": []},
            "BruteForce": {"insert": [], "search": [], "range": [], "memory": []}
        }

        for size in key_sizes:
            print(f"Benchmarking for {size} keys...")
            time_results, used_keys = self.measure_time(size)
            mem_results = self.measure_memory(used_keys)

            for ds in ["B+ Tree", "BruteForce"]:
                all_stats[ds]["insert"].append(time_results[ds]["insert"])
                all_stats[ds]["search"].append(time_results[ds]["search"])
                all_stats[ds]["range"].append(time_results[ds]["range"])
                all_stats[ds]["memory"].append(mem_results[ds]["memory"])

        self.plot_results(all_stats, key_sizes)
        return all_stats

    def plot_results(self, stats, sizes):
        fig, axs = plt.subplots(2, 2, figsize=(14, 10))
        fig.suptitle('B+ Tree vs BruteForceDB Performance Scaling', fontsize=16)

        operations = [
            ("insert", "Insertion Time (Seconds)", axs[0, 0]),
            ("search", "Search Time (Seconds)", axs[0, 1]),
            ("range", "Range Query Time (Seconds)", axs[1, 0]),
            ("memory", "Peak Memory Usage (MB)", axs[1, 1])
        ]

        for op, ylabel, ax in operations:
            ax.plot(sizes, stats["B+ Tree"][op], label='B+ Tree', marker='o', linewidth=2, color='#1f77b4')
            ax.plot(sizes, stats["BruteForce"][op], label='Brute Force', marker='s', linewidth=2, color='#ff7f0e')
            ax.set_title(f'{op.capitalize()} Performance')
            ax.set_xlabel('Number of Keys')
            ax.set_ylabel(ylabel)
            ax.grid(True, linestyle='--', alpha=0.7)
            ax.legend()
            ax.ticklabel_format(style='plain', axis='x')

        plt.tight_layout(rect=[0, 0.03, 1, 0.95])
        plt.show()


if __name__ == "__main__":
    analyzer = PerformanceAnalyzer()

    sizes = [i * 100 for i in range(1, 500)]

    analyzer.run_scaling_benchmarks(sizes)
