""" Compress an image using Voronoi cells """
import numpy as np
from scipy.spatial import Voronoi
import matplotlib.pyplot as plt
from matplotlib.pyplot import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
import cv2
from sortedcontainers import SortedSet
from os import path
import sys
import pickle
from itertools import product, chain, count, cycle
from math import ceil
from utils import *

import pdb

script_folder, _ = path.split(path.abspath(__file__))
raw_images_folder = path.join(script_folder, 'images', 'raw')
compressed_images_folder = path.join(script_folder, 'images', 'compressed')
decompressed_images_folder = path.join(script_folder, 'images', 'decompressed')


def debug_print(value, enabled=False, *args, **kwargs):
    if enabled:
        print(value, *args, *kwargs)


# region Compression

def compress(raw_image_file, compressed_image_file, verbose=False):

    image_data = cv2.imread(raw_image_file)

    if image_data is None or len(image_data) == 0:
        raise FileNotFoundError(f'Image {raw_image_file} was not found')

    image_cell_grid(image_data, verbose=verbose)

    original_n_edges = len(Cell.edge_set)
    n_edges = original_n_edges // 2

    # TODO: Fix merging ValueError/KeyError issue
    # TODO: Make merging criteria more nuanced - currently colours bleed as compressed cells seem more similar to local
    # cells as they are further compressed
    debug_print('Merging similar cells', enabled=verbose)
    while len(Cell.edge_set) > n_edges:
        Cell.merge_cells(*Cell.least_difference_edge())
        print_progress(original_n_edges - len(Cell.edge_set), original_n_edges - n_edges, enabled=verbose)

    # TODO: Save compressed data in VSA file - must come up with serialization scheme which saves space
    # TODO: Break up functionality into a decompress function

    cell_set = set()
    debug_print(f'Gathering reduced vertex set', enabled=verbose)
    for first_cell, second_cell in Cell.edge_set:
        cell_set.add(first_cell)
        cell_set.add(second_cell)
        print_progress(len(cell_set), n_edges)
    print_progress(n_edges, n_edges)
    cell_list = list(cell_set)

    debug_print(f'Rasterizing Voronoi diagram', enabled=verbose)
    compressed_image = voronoi_fill(cell_list, image_data, verbose=verbose)

    debug_print(f'Saving compressed image to {compressed_image_file}', enabled=verbose)
    cv2.imwrite(compressed_image_file, compressed_image)

# endregion


# region Voronoi cells

class Cell:

    edge_set = SortedSet(key=lambda cells: sum([cell.weight for cell in cells]) * Cell.compare_colours(*cells))

    @staticmethod
    def add_edge(first_cell, second_cell):
        Cell.edge_set.add(frozenset([first_cell, second_cell]))
        first_cell._neighbours.add(second_cell)
        second_cell._neighbours.add(first_cell)


    @staticmethod
    def remove_edge(first_cell, second_cell):
        Cell.edge_set.remove(frozenset([first_cell, second_cell]))
        first_cell._neighbours.remove(second_cell)
        second_cell._neighbours.remove(first_cell)

    @staticmethod
    def remove_cell(cell):
        for neighbour in list(cell._neighbours):
            Cell.remove_edge(cell, neighbour)

    @staticmethod
    def least_difference_edge():
        return Cell.edge_set[0]

    @staticmethod
    def compare_colours(first_cell, second_cell):
        return np.linalg.norm(first_cell.colour - second_cell.colour)

    @staticmethod
    def merge_cells(first_cell, second_cell):

        try:
            Cell.remove_edge(first_cell, second_cell)
        except ValueError:
            ''' Ignore this clause - we only need to disconnect the cells if they are connected '''

        weights = [first_cell.weight, second_cell.weight]

        colour = weighted_vector_average([first_cell.colour, second_cell.colour], weights=weights)
        position = weighted_vector_average([first_cell.position, second_cell.position], weights=weights)
        weight = sum(weights)
        neighbours = {*first_cell._neighbours, *second_cell._neighbours}

        Cell.remove_cell(first_cell)
        Cell.remove_cell(second_cell)

        new_cell = Cell(colour, position, weight, neighbours)

        return new_cell

    def __init__(self, colour, position=None, weight=1, neighbours=set()):
        self._colour = colour
        self.position = position
        self.weight = weight
        self._neighbours = set()
        for cell in neighbours:
            Cell.add_edge(self, cell)

    def __repr__(self):
        representation = f'cell(c={self._colour:}, p={self.position}, w={self.weight})'
        return representation

    @property
    def colour(self):
        return self._colour

    @colour.setter
    def colour(self, colour):
        raise NotImplemented()  # Implementing this would require updating all neighbours and the edge set

    def least_difference_neighbour(self):
        return self._neighbours[0]

    # region Aliases

    @property
    def c(self):
        return self.colour

    @c.setter
    def c(self, colour):
        self.colour = colour

    @property
    def p(self):
        return self.position

    @p.setter
    def p(self, position):
        self.position = position

    @property
    def w(self):
        return self.weight

    @w.setter
    def w(self, weight):
        self.weight = weight

    # endregion


def image_cell_grid(image_data, verbose=False):
    debug_print(f'Loading cell grid', enabled=verbose)
    height, width, colour_dimension = image_data.shape
    cells = [[Cell(colour=image_data[i, j], position=np.array([i, j], dtype=np.float64))
              for j in range(width)] for i in range(height)]
    for i, row in enumerate(cells):
        for j, cell in enumerate(row):
            for neighbour in grid_neighbours(cells, i, j):
                Cell.add_edge(cell, neighbour)
            print_progress(i * width + j + 1, height * width, enabled=verbose)
    return cells


def voronoi_fill(cells, image_data, verbose=False):
    # TODO: Add weighting
    # TODO: Add gradient-edge relations (use gradient of original image/etc) - can be done through key of edge_set

    # Divide the image into overlapping neighbourhoods, group cells by their membership in neighbourhoods, to find the
    # closest cell for any pixel evaluate all cells in the neighborhood

    # https://stackoverflow.com/questions/16024428/reference-algorithm-for-weighted-voronoi-diagrams
    # https://stackoverflow.com/questions/53696900/render-voronoi-diagram-to-numpy-array

    height, width, colour_dim = shape = image_data.shape

    neighborhood_size = 10
    neighborhoods_height = int(ceil(height / (neighborhood_size/2)) - 1)
    neighborhoods_width = int(ceil(width / (neighborhood_size/2)) - 1)

    cell_neighborhoods = [[set() for _ in range(neighborhoods_width)] for _ in range(neighborhoods_height)]

    def nearby_neighborhood_indices(row, col, degree=1):
        neighborhood_row = bound_to_range(int((row - neighborhood_size/4) / (neighborhood_size/2)),
                                          minimum=0, maximum=neighborhoods_height)
        neighborhood_col = bound_to_range(int((col - neighborhood_size/4) / (neighborhood_size/2)),
                                          minimum=0, maximum=neighborhoods_width)

        if degree == 0:
            return [(neighborhood_row, neighborhood_col)]

        offsets = [-degree, degree]
        top_left, top_right, bottom_left, bottom_right = [(neighborhood_row + i, neighborhood_col + j)
                                                          for i, j in product(offsets, offsets)]

        top = zip(cycle([top_left[0]]), range(top_left[1], top_right[1], 2))
        bottom = zip(cycle([bottom_left[0]]), range(bottom_left[1] + 1, bottom_right[1] + 1, 2))
        left = zip(range(top_left[0] + 1, bottom_left[0] + 1, 2), cycle([top_left[1]]))
        right = zip(range(top_right[0], bottom_right[0], 2), cycle([top_right[1]]))

        return [(i, j) for i, j in chain(top, bottom, left, right) if inbounds(i, j, neighborhoods_height,
                                                                               neighborhoods_width)]

    def nearby_neighborhoods(row, col, degree=1):
        return [cell_neighborhoods[i][j] for i, j in nearby_neighborhood_indices(row, col, degree=degree)]

    debug_print('Populating cell neighborhoods', enabled=verbose)
    for i, cell in enumerate(cells):
        # TODO: The cell belongs to the deg 0 neighborhood and ONE of the deg 1 neighborhoods, fix this code accordingly
        for neighborhood in nearby_neighborhoods(*cell.position, degree=0) + \
                            nearby_neighborhoods(*cell.position, degree=1):
            neighborhood.add(cell)
        print_progress(i + 1, len(cells), enabled=verbose)

    def closest_cell(row, col):
        neighborhood = nearby_neighborhoods(row, col, degree=1)
        degree = count(2)
        while not any(neighborhood):
            neighborhood = nearby_neighborhoods(row, col, degree=next(degree))
        return min(chain(*neighborhood), key=lambda cell: np.linalg.norm(cell.position - np.array([row, col])))

    filled = np.empty(shape)

    debug_print('Populating Voronoi array', enabled=verbose)
    for i in range(height):
        for j in range(width):
            filled[i, j] = closest_cell(i, j).colour
            print_progress(i * width + j + 1, height * width, enabled=verbose)

    return filled

# endregion


if __name__ == '__main__':

    try:
        raw_image_name = sys.argv[1]
        raw_image = path.join(raw_images_folder, raw_image_name)
        if not path.exists(raw_image):
            print(f'File {sys.argv[1]} not found')
            sys.exit(1)

    except IndexError:
        raw_image_name = input('Enter an image name: ')
        raw_image = path.join(raw_images_folder, raw_image_name)
        while not path.exists(raw_image):
            raw_image_name = input('File not found, try another: ')
            raw_image = path.join(raw_images_folder, raw_image_name)

    compressed_image_name = raw_image_name
    compressed_image = path.join(compressed_images_folder, compressed_image_name)

    compress(raw_image, compressed_image, verbose=True)

    # # TODO: Add back in when compression functionality is broken up
    # decompressed_image_name = compressed_image_name
    # decompressed_image = path.join(decompressed_images_folder, compressed_image_name)
    #
    # decompress(compressed_image, decompressed_image, verbose=True)
