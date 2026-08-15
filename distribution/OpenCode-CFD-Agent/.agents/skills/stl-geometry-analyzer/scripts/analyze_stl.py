#!/usr/bin/env python3
"""Analyze STL geometry for CFD mesh planning using only Python stdlib."""

from __future__ import annotations

import argparse
import heapq
import json
import math
import struct
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

Vec = tuple[float, float, float]
Tri = tuple[Vec, Vec, Vec]
EPS = 1.0e-15


def add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def mul(a: Vec, value: float) -> Vec:
    return (a[0] * value, a[1] * value, a[2] * value)


def dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def norm2(a: Vec) -> float:
    return dot(a, a)


def distance(a: Vec, b: Vec) -> float:
    return math.sqrt(norm2(sub(a, b)))


def unit(a: Vec) -> Vec:
    length = math.sqrt(norm2(a))
    return mul(a, 1.0 / length) if length > EPS else (0.0, 0.0, 0.0)


def tri_normal(tri: Tri) -> Vec:
    return unit(cross(sub(tri[1], tri[0]), sub(tri[2], tri[0])))


def tri_area(tri: Tri) -> float:
    return 0.5 * math.sqrt(norm2(cross(sub(tri[1], tri[0]), sub(tri[2], tri[0]))))


def tri_centroid(tri: Tri) -> Vec:
    return mul(add(add(tri[0], tri[1]), tri[2]), 1.0 / 3.0)


def tri_bounds(tri: Tri) -> tuple[Vec, Vec]:
    return tuple(min(p[i] for p in tri) for i in range(3)), tuple(max(p[i] for p in tri) for i in range(3))


def combine_bounds(items: Iterable[tuple[Vec, Vec]]) -> tuple[Vec, Vec]:
    values = list(items)
    return (
        tuple(min(value[0][i] for value in values) for i in range(3)),
        tuple(max(value[1][i] for value in values) for i in range(3)),
    )


def bbox_distance2(a: tuple[Vec, Vec], b: tuple[Vec, Vec]) -> float:
    total = 0.0
    for axis in range(3):
        if a[1][axis] < b[0][axis]:
            delta = b[0][axis] - a[1][axis]
        elif b[1][axis] < a[0][axis]:
            delta = a[0][axis] - b[1][axis]
        else:
            delta = 0.0
        total += delta * delta
    return total


@dataclass
class Facet:
    vertices: Tri
    region: str
    source: str


def is_binary_stl(path: Path) -> bool:
    size = path.stat().st_size
    if size < 84:
        return False
    with path.open("rb") as stream:
        stream.seek(80)
        raw = stream.read(4)
    return len(raw) == 4 and 84 + 50 * struct.unpack("<I", raw)[0] == size


def read_binary(path: Path) -> list[Facet]:
    facets: list[Facet] = []
    with path.open("rb") as stream:
        stream.read(80)
        count = struct.unpack("<I", stream.read(4))[0]
        for _ in range(count):
            values = struct.unpack("<12fH", stream.read(50))
            tri = ((values[3], values[4], values[5]), (values[6], values[7], values[8]), (values[9], values[10], values[11]))
            facets.append(Facet(tri, "binary", str(path)))
    return facets


def read_ascii(path: Path) -> list[Facet]:
    facets: list[Facet] = []
    region = "default"
    vertices: list[Vec] = []
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for raw in stream:
            parts = raw.strip().split()
            if not parts:
                continue
            if parts[0].lower() == "solid":
                region = " ".join(parts[1:]).strip() or "default"
            elif parts[0].lower() == "vertex" and len(parts) >= 4:
                vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
                if len(vertices) == 3:
                    facets.append(Facet(tuple(vertices), region, str(path)))
                    vertices = []
    if vertices:
        raise ValueError(f"incomplete facet in ASCII STL: {path}")
    if not facets:
        raise ValueError(f"no facets found in STL: {path}")
    return facets


def read_stl(path: Path) -> list[Facet]:
    return read_binary(path) if is_binary_stl(path) else read_ascii(path)


def resolve_inputs(inputs: Sequence[str]) -> list[Path]:
    paths: list[Path] = []
    for item in inputs:
        path = Path(item).resolve()
        if path.is_dir():
            paths.extend(sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() == ".stl"))
        elif path.is_file() and path.suffix.lower() == ".stl":
            paths.append(path)
        else:
            raise ValueError(f"not an STL file or directory: {path}")
    unique = list(dict.fromkeys(paths))
    if not unique:
        raise ValueError("no STL files found")
    return unique


class UnionFind:
    def __init__(self, size: int):
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a: int, b: int) -> None:
        a, b = self.find(a), self.find(b)
        if a != b:
            self.parent[b] = a


def vertex_keys(facets: Sequence[Facet], tolerance: float) -> list[tuple[tuple[int, int, int], ...]]:
    scale = 1.0 / max(tolerance, EPS)
    return [tuple(tuple(round(value * scale) for value in vertex) for vertex in facet.vertices) for facet in facets]


def component_ids(keys: Sequence[tuple[tuple[int, int, int], ...]]) -> list[int]:
    union = UnionFind(len(keys))
    owners: dict[tuple[int, int, int], int] = {}
    for index, tri_keys in enumerate(keys):
        for key in tri_keys:
            if key in owners:
                union.union(index, owners[key])
            else:
                owners[key] = index
    return [union.find(index) for index in range(len(keys))]


def point_triangle_distance2(point: Vec, tri: Tri) -> float:
    a, b, c = tri
    ab, ac, ap = sub(b, a), sub(c, a), sub(point, a)
    d1, d2 = dot(ab, ap), dot(ac, ap)
    if d1 <= 0 and d2 <= 0:
        return norm2(ap)
    bp = sub(point, b)
    d3, d4 = dot(ab, bp), dot(ac, bp)
    if d3 >= 0 and d4 <= d3:
        return norm2(bp)
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        return norm2(sub(point, add(a, mul(ab, d1 / (d1 - d3)))))
    cp = sub(point, c)
    d5, d6 = dot(ab, cp), dot(ac, cp)
    if d6 >= 0 and d5 <= d6:
        return norm2(cp)
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        return norm2(sub(point, add(a, mul(ac, d2 / (d2 - d6)))))
    va = d3 * d6 - d5 * d4
    if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
        edge = sub(c, b)
        return norm2(sub(point, add(b, mul(edge, (d4 - d3) / ((d4 - d3) + (d5 - d6))))))
    denom = 1.0 / (va + vb + vc)
    closest = add(a, add(mul(ab, vb * denom), mul(ac, vc * denom)))
    return norm2(sub(point, closest))


def segment_segment_distance2(p1: Vec, q1: Vec, p2: Vec, q2: Vec) -> float:
    d1, d2, r = sub(q1, p1), sub(q2, p2), sub(p1, p2)
    a, e, f = dot(d1, d1), dot(d2, d2), dot(d2, r)
    if a <= EPS and e <= EPS:
        return norm2(r)
    if a <= EPS:
        s, t = 0.0, min(1.0, max(0.0, f / e))
    else:
        c = dot(d1, r)
        if e <= EPS:
            t, s = 0.0, min(1.0, max(0.0, -c / a))
        else:
            b = dot(d1, d2)
            denom = a * e - b * b
            s = min(1.0, max(0.0, (b * f - c * e) / denom)) if abs(denom) > EPS else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                t, s = 0.0, min(1.0, max(0.0, -c / a))
            elif t > 1.0:
                t, s = 1.0, min(1.0, max(0.0, (b - c) / a))
    return norm2(sub(add(p1, mul(d1, s)), add(p2, mul(d2, t))))


def segment_hits_triangle(p: Vec, q: Vec, tri: Tri) -> bool:
    direction = sub(q, p)
    edge1, edge2 = sub(tri[1], tri[0]), sub(tri[2], tri[0])
    h = cross(direction, edge2)
    determinant = dot(edge1, h)
    if abs(determinant) <= EPS:
        return False
    inv = 1.0 / determinant
    s = sub(p, tri[0])
    u = inv * dot(s, h)
    if not 0.0 <= u <= 1.0:
        return False
    qvec = cross(s, edge1)
    v = inv * dot(direction, qvec)
    if v < 0.0 or u + v > 1.0:
        return False
    t = inv * dot(edge2, qvec)
    return -EPS <= t <= 1.0 + EPS


def triangle_distance2(a: Tri, b: Tri) -> float:
    edges_a = ((a[0], a[1]), (a[1], a[2]), (a[2], a[0]))
    edges_b = ((b[0], b[1]), (b[1], b[2]), (b[2], b[0]))
    if any(segment_hits_triangle(p, q, b) for p, q in edges_a) or any(segment_hits_triangle(p, q, a) for p, q in edges_b):
        return 0.0
    values = [point_triangle_distance2(point, b) for point in a]
    values.extend(point_triangle_distance2(point, a) for point in b)
    values.extend(segment_segment_distance2(*ea, *eb) for ea in edges_a for eb in edges_b)
    return min(values)


@dataclass
class BvhNode:
    indices: tuple[int, ...]
    bounds: tuple[Vec, Vec]
    left: "BvhNode | None" = None
    right: "BvhNode | None" = None

    @property
    def leaf(self) -> bool:
        return self.left is None


def build_bvh(indices: Sequence[int], bounds: Sequence[tuple[Vec, Vec]], centroids: Sequence[Vec]) -> BvhNode:
    node_bounds = combine_bounds(bounds[index] for index in indices)
    if len(indices) <= 8:
        return BvhNode(tuple(indices), node_bounds)
    extents = sub(node_bounds[1], node_bounds[0])
    axis = max(range(3), key=lambda item: extents[item])
    ordered = sorted(indices, key=lambda item: centroids[item][axis])
    middle = len(ordered) // 2
    return BvhNode(tuple(indices), node_bounds, build_bvh(ordered[:middle], bounds, centroids), build_bvh(ordered[middle:], bounds, centroids))


def minimum_clearance(facets: Sequence[Facet], keys: Sequence[tuple[tuple[int, int, int], ...]], max_normal_dot: float) -> dict | None:
    if len(facets) < 2:
        return None
    triangles = [facet.vertices for facet in facets]
    bounds = [tri_bounds(triangle) for triangle in triangles]
    centroids = [tri_centroid(triangle) for triangle in triangles]
    normals = [tri_normal(triangle) for triangle in triangles]
    root = build_bvh(range(len(facets)), bounds, centroids)
    heap: list[tuple[float, int, BvhNode, BvhNode]] = []
    serial = 0

    def push(a: BvhNode, b: BvhNode) -> None:
        nonlocal serial
        serial += 1
        heapq.heappush(heap, (bbox_distance2(a.bounds, b.bounds), serial, a, b))

    push(root, root)
    best = math.inf
    pair: tuple[int, int] | None = None
    while heap:
        lower, _, a, b = heapq.heappop(heap)
        if lower >= best:
            continue
        if a is b:
            if a.leaf:
                candidates = ((a.indices[i], a.indices[j]) for i in range(len(a.indices)) for j in range(i + 1, len(a.indices)))
                for i, j in candidates:
                    if set(keys[i]).intersection(keys[j]) or dot(normals[i], normals[j]) > max_normal_dot:
                        continue
                    value = triangle_distance2(triangles[i], triangles[j])
                    if value < best:
                        best, pair = value, (i, j)
            else:
                push(a.left, a.left)
                push(a.left, a.right)
                push(a.right, a.right)
        elif a.leaf and b.leaf:
            for i in a.indices:
                for j in b.indices:
                    if set(keys[i]).intersection(keys[j]) or dot(normals[i], normals[j]) > max_normal_dot:
                        continue
                    value = triangle_distance2(triangles[i], triangles[j])
                    if value < best:
                        best, pair = value, (i, j)
        elif b.leaf or (not a.leaf and len(a.indices) >= len(b.indices)):
            push(a.left, b)
            push(a.right, b)
        else:
            push(a, b.left)
            push(a, b.right)
    if pair is None:
        return None
    i, j = pair
    return {
        "distance": math.sqrt(best),
        "triangle_indices": [i, j],
        "regions": [facets[i].region, facets[j].region],
        "sources": [facets[i].source, facets[j].source],
        "normal_dot": dot(normals[i], normals[j]),
        "approximate_location": list(mul(add(centroids[i], centroids[j]), 0.5)),
        "method": "triangle distance with BVH; shared-vertex pairs excluded; opposed-normal filter applied",
    }


def percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def bounds_json(bounds: tuple[Vec, Vec]) -> dict:
    extents = sub(bounds[1], bounds[0])
    return {"min": list(bounds[0]), "max": list(bounds[1]), "extents": list(extents), "diagonal": math.sqrt(norm2(extents))}


def analyze(paths: Sequence[Path], units: str, cells_across_gap: int, max_normal_dot: float) -> dict:
    facets: list[Facet] = []
    inputs = []
    for path in paths:
        loaded = read_stl(path)
        facets.extend(loaded)
        inputs.append({"path": str(path), "format": "binary" if is_binary_stl(path) else "ascii", "triangles": len(loaded)})
    overall = combine_bounds(tri_bounds(facet.vertices) for facet in facets)
    diagonal = bounds_json(overall)["diagonal"]
    tolerance = max(diagonal * 1.0e-9, 1.0e-12)
    keys = vertex_keys(facets, tolerance)
    components = component_ids(keys)
    edge_lengths = [distance(tri[a], tri[b]) for tri in (facet.vertices for facet in facets) for a, b in ((0, 1), (1, 2), (2, 0))]
    regions: dict[str, list[int]] = defaultdict(list)
    for index, facet in enumerate(facets):
        regions[f"{Path(facet.source).name}:{facet.region}"].append(index)
    region_info = []
    for name, indices in sorted(regions.items()):
        region_info.append({
            "name": name,
            "triangles": len(indices),
            "surface_area": sum(tri_area(facets[index].vertices) for index in indices),
            "bounds": bounds_json(combine_bounds(tri_bounds(facets[index].vertices) for index in indices)),
        })
    clearance = minimum_clearance(facets, keys, max_normal_dot)
    return {
        "schema_version": 1,
        "coordinate_units": units,
        "inputs": inputs,
        "triangles": len(facets),
        "bounds": bounds_json(overall),
        "surface_area": sum(tri_area(facet.vertices) for facet in facets),
        "signed_volume_estimate": sum(dot(f.vertices[0], cross(f.vertices[1], f.vertices[2])) / 6.0 for f in facets),
        "edge_lengths": {"min": min(edge_lengths), "p05": percentile(edge_lengths, 0.05), "median": percentile(edge_lengths, 0.5), "p95": percentile(edge_lengths, 0.95), "max": max(edge_lengths)},
        "topology": {"connected_components": len(set(components)), "vertex_merge_tolerance": tolerance},
        "regions": region_info,
        "minimum_opposed_clearance": clearance,
        "mesh_guidance": {
            "cells_across_gap": cells_across_gap,
            "maximum_cell_size_for_gap": clearance["distance"] / cells_across_gap if clearance else None,
            "rule": "target cell size <= estimated opposed clearance / cells_across_gap",
        },
        "limitations": [
            "Clearance is measured on the triangulation, not the source CAD.",
            "Shared-vertex pairs are excluded and only opposed normals are considered.",
            "Dirty, intersecting, duplicated, inconsistently oriented, or coarse facets can mislead the estimate.",
            "Units are user-declared and are not inferred from the file name.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", help="STL files or directories")
    parser.add_argument("--units", default="unspecified")
    parser.add_argument("--cells-across-gap", type=int, default=4)
    parser.add_argument("--max-normal-dot", type=float, default=-0.3)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cells_across_gap < 1:
        raise SystemExit("--cells-across-gap must be positive")
    if not -1.0 <= args.max_normal_dot <= 1.0:
        raise SystemExit("--max-normal-dot must be between -1 and 1")
    try:
        report = analyze(resolve_inputs(args.inputs), args.units, args.cells_across_gap, args.max_normal_dot)
    except (OSError, ValueError, struct.error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8", newline="\n")
        print(output)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
