import time
import tracemalloc
import math
import random
import gc
import matplotlib.pyplot as plt
from bplustree import BPlusTree
from bruteforce import BruteForceDB

random.seed(42)

class PerformanceAnalyzer:

    def run_scaling_benchmarks(self, key_sizes):
        all_stats = {
            "B+ Tree": {"insert": [], "search": [], "range": [], "memory": []},
            "BruteForce": {"insert": [], "search": [], "range": [], "memory": []}
        }

        # Build incrementally one tree for the whole run
        bptree = BPlusTree(order=7)
        brutedb = BruteForceDB()

        inserted_keys = []
        prev_size = 0

        for size in key_sizes:
            print(f"Benchmarking for {size} keys...")

            new_keys = random.sample(range(1, 1_000_000), size - prev_size)
            inserted_keys.extend(new_keys)
            prev_size = size

            # Insert 
            start = time.perf_counter()
            for k in new_keys:
                bptree.insert(k, f"val_{k}")
            all_stats["B+ Tree"]["insert"].append(time.perf_counter() - start)

            start = time.perf_counter()
            for k in new_keys:
                brutedb.insert(k)
            all_stats["BruteForce"]["insert"].append(time.perf_counter() - start)

            # Search
            search_keys = random.sample(inserted_keys, min(200, len(inserted_keys)))

            start = time.perf_counter()
            for k in search_keys:
                bptree.search(k)
            all_stats["B+ Tree"]["search"].append(time.perf_counter() - start)

            start = time.perf_counter()
            for k in search_keys:
                brutedb.search(k)
            all_stats["BruteForce"]["search"].append(time.perf_counter() - start)

            # Range Query 
            r_start, r_end = min(inserted_keys), min(inserted_keys) + size // 2
            start = time.perf_counter()
            bptree.range_query(r_start, r_end)
            all_stats["B+ Tree"]["range"].append(time.perf_counter() - start)

            start = time.perf_counter()
            brutedb.range_query(r_start, r_end)
            all_stats["BruteForce"]["range"].append(time.perf_counter() - start)

            # Memory 
            gc.collect()
            tracemalloc.start()
            tmp = BPlusTree(order=50)
            for k in inserted_keys:
                tmp.insert(k, f"val_{k}")
            _, peak_bpt = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            del tmp

            gc.collect()
            tracemalloc.start()
            tmp_bf = BruteForceDB()
            for k in inserted_keys:
                tmp_bf.insert(k)
            _, peak_bf = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            del tmp_bf

            all_stats["B+ Tree"]["memory"].append(peak_bpt / (1024 * 1024))
            all_stats["BruteForce"]["memory"].append(peak_bf / (1024 * 1024))

        self.plot_results(all_stats, key_sizes)
        return all_stats

    def plot_results(self, stats, sizes):
        fig, axs = plt.subplots(2, 2, figsize=(14, 10))
        fig.suptitle('B+ Tree vs Brute Force', fontsize=16)

        operations = [
            ("insert", "Insertion Time (Seconds)", axs[0, 0]),
            ("search", "Search Time (Seconds)",    axs[0, 1]),
            ("range",  "Range Query Time (Seconds)", axs[1, 0]),
            ("memory", "Peak Memory Usage (MB)",   axs[1, 1]),
        ]

        for op, ylabel, ax in operations:
            ax.plot(sizes, stats["B+ Tree"][op],   label='B+ Tree',    marker='o', linewidth=2, color='#1f77b4')
            ax.plot(sizes, stats["BruteForce"][op], label='Brute Force', marker='s', linewidth=2, color='#ff7f0e')
            ax.set_title(f'{op.capitalize()} Performance')
            ax.set_xlabel('Number of Keys')
            ax.set_ylabel(ylabel)
            ax.grid(True, linestyle='--', alpha=0.7)
            ax.legend()
            ax.ticklabel_format(style='plain', axis='x')

        plt.tight_layout(rect=[0, 0.03, 1, 0.95])
        plt.savefig("../images/performance_plot.png", dpi=150)
        plt.show()


if __name__ == "__main__":
    analyzer = PerformanceAnalyzer()

    sizes = [100, 500, 1000, 2000, 5000, 10000, 20000, 35000, 50000, 75000, 100000]    
    analyzer.run_scaling_benchmarks(sizes)