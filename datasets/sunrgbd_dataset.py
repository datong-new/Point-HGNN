"""
from mmdet3d.datasets.sunrgbd_dataset.py
"""
import numpy as np
from os import path as osp

from mmdet3d.core import show_result
from mmdet3d.core.bbox import DepthInstance3DBoxes
from mmdet.datasets import DATASETS

import sys
sys.path.insert(1, './datasets')
from custom_3d import Custom3DDataset

from construct_graph import voxelize, inter_level_graph, intra_level_graph


# @DATASETS.register_module()
class SUNRGBDDataset(Custom3DDataset):
    r"""SUNRGBD Dataset.

    This class serves as the API for experiments on the SUNRGBD Dataset.

    See the `download page <http://rgbd.cs.princeton.edu/challenge.html>`_
    for data downloading.

    Args:
        data_root (str): Path of dataset root.
        ann_file (str): Path of annotation file.
        pipeline (list[dict], optional): Pipeline used for data processing.
            Defaults to None.
        classes (tuple[str], optional): Classes used in the dataset.
            Defaults to None.
        modality (dict, optional): Modality to specify the sensor data used
            as input. Defaults to None.
        box_type_3d (str, optional): Type of 3D box of this dataset.
            Based on the `box_type_3d`, the dataset will encapsulate the box
            to its original format then converted them to `box_type_3d`.
            Defaults to 'Depth' in this dataset. Available options includes

            - 'LiDAR': Box in LiDAR coordinates.
            - 'Depth': Box in depth coordinates, usually for indoor dataset.
            - 'Camera': Box in camera coordinates.
        filter_empty_gt (bool, optional): Whether to filter empty GT.
            Defaults to True.
        test_mode (bool, optional): Whether the dataset is in test mode.
            Defaults to False.
    """
    CLASSES = ('bed', 'table', 'sofa', 'chair', 'toilet', 'desk', 'dresser',
               'night_stand', 'bookshelf', 'bathtub')

    def __init__(self,
                 data_root,
                 ann_file,
                 downsample_voxel_sizes=[[0.1, 0.1, 0.1], [0.3, 0.3, 0.3], [0.5, 0.5, 0.5]],
                 inter_radius=[0.3, 0.5, 0.7],
                 intra_radius=[0.4, 0.6, 0.8],
                 max_num_neighbors=32,
                 pipeline=None,
                 classes=None,
                 modality=None,
                 box_type_3d='Depth',
                 filter_empty_gt=True,
                 test_mode=False):
        super().__init__(
            data_root=data_root,
            ann_file=ann_file,
            pipeline=pipeline,
            classes=classes,
            modality=modality,
            box_type_3d=box_type_3d,
            filter_empty_gt=filter_empty_gt,
            test_mode=test_mode)
        # print('Welcome to customized dataset.')

        # parameter for construct graph
        self.downsample_voxel_sizes = downsample_voxel_sizes
        self.inter_radius = inter_radius
        self.intra_radius = intra_radius
        self.max_num_neighbors = max_num_neighbors

    def get_levels_coordinates(self, point_coordinates, voxel_sizes):
        """
        args:
            point_coordinates: a tensor, N x 3
            voxel_sizes: a list of list, its length is 3 defaut;
                example: [[0.05,0.05,0.1], [0.07 , 0.07, 0.12], [0.09, 0.09, 0.14]]
        return:
            downsample_point_coordinates: a list of tensor, whose length is the same as voxel.

        """
        l1_point_coordinates, l1_point_indices = voxelize(point_coordinates, voxel_sizes[0])
        l2_point_coordinates, l2_point_indices = voxelize(point_coordinates, voxel_sizes[1])
        l3_point_coordinates, l3_point_indices = voxelize(point_coordinates, voxel_sizes[2])

        return [l1_point_coordinates, l2_point_coordinates, l3_point_coordinates], \
                [l1_point_indices, l2_point_indices, l3_point_indices]

    def construct_graph(self, points):
        # points = points[0][:100, :]
        # points = points[0]
        coordinates, indices = self.get_levels_coordinates(points[:, :3], self.downsample_voxel_sizes)
        coordinates = [points[:, :3]] + coordinates
        inter_graphs = {}
        intra_graphs = {}
        for i in range(len(coordinates)):
            if i != len(coordinates) - 1:
                inter_graphs["{}_{}".format(i, i + 1)], inter_graphs["{}_{}".format(i + 1, i)] = \
                    inter_level_graph(coordinates[i], coordinates[i + 1], self.inter_radius[i],
                                      max_num_neighbors=self.max_num_neighbors)
            if i != 0:
                # construct intra graph
                intra_graphs["{}_{}".format(i, i)] = intra_level_graph(coordinates[i], self.intra_radius[i - 1])
        return coordinates, indices, inter_graphs, intra_graphs

    def __getitem__(self, idx):
        """Get item from infos according to the given index.

        Returns:
            dict: Data dictionary of the corresponding index.
        """
        if self.test_mode:
            return self.prepare_test_data(idx)
        while True:
            data = self.prepare_train_data(idx)
            if data is None:
                idx = self._rand_another(idx)
                continue
            points = data['points'].data
            points = data['points'].data[:100, :]
            # print(type(points))
            # print(points.size())
            # print(points)
            coordinates, indices, inter_graphs, intra_graphs = self.construct_graph(points)
            from mmcv.parallel.data_container import DataContainer
            # data['coordinates'] = coordinates
            # data['indices'] = indices
            # data['inter_graphs'] = inter_graphs
            # data['intra_graphs'] = intra_graphs
            # print('before:', data)
            data['coordinates'] = DataContainer(coordinates)
            data['indices'] = DataContainer(indices)
            data['inter_graphs'] = DataContainer(inter_graphs)
            data['intra_graphs'] = DataContainer(intra_graphs)
            # print('raw data:', data)
            # print('after:', data)
            return data

    def get_ann_info(self, index):
        """Get annotation info according to the given index.

        Args:
            index (int): Index of the annotation data to get.

        Returns:
            dict: annotation information consists of the following keys:

                - gt_bboxes_3d (:obj:`DepthInstance3DBoxes`): \
                    3D ground truth bboxes
                - gt_labels_3d (np.ndarray): Labels of ground truths.
                - pts_instance_mask_path (str): Path of instance masks.
                - pts_semantic_mask_path (str): Path of semantic masks.
        """
        # Use index to get the annos, thus the evalhook could also use this api
        info = self.data_infos[index]
        if info['annos']['gt_num'] != 0:
            gt_bboxes_3d = info['annos']['gt_boxes_upright_depth'].astype(
                np.float32)  # k, 6
            gt_labels_3d = info['annos']['class'].astype(np.long)
        else:
            gt_bboxes_3d = np.zeros((0, 7), dtype=np.float32)
            gt_labels_3d = np.zeros((0, ), dtype=np.long)

        # to target box structure
        gt_bboxes_3d = DepthInstance3DBoxes(
            gt_bboxes_3d, origin=(0.5, 0.5, 0.5)).convert_to(self.box_mode_3d)

        anns_results = dict(
            gt_bboxes_3d=gt_bboxes_3d, gt_labels_3d=gt_labels_3d)
        return anns_results

    def show(self, results, out_dir):
        """Results visualization.

        Args:
            results (list[dict]): List of bounding boxes results.
            out_dir (str): Output directory of visualization result.
        """
        assert out_dir is not None, 'Expect out_dir, got none.'
        for i, result in enumerate(results):
            data_info = self.data_infos[i]
            pts_path = data_info['pts_path']
            file_name = osp.split(pts_path)[-1].split('.')[0]
            points = np.fromfile(
                osp.join(self.data_root, pts_path),
                dtype=np.float32).reshape(-1, 6)
            points[:, 3:] *= 255
            if data_info['annos']['gt_num'] > 0:
                gt_bboxes = data_info['annos']['gt_boxes_upright_depth']
            else:
                gt_bboxes = np.zeros((0, 7))
            pred_bboxes = result['boxes_3d'].tensor.numpy()
            pred_bboxes[..., 2] += pred_bboxes[..., 5] / 2
            show_result(points, gt_bboxes, pred_bboxes, out_dir, file_name)