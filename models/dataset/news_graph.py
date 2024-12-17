import numpy as np
import os
import scipy.sparse as sp
import torch
from dataset import utils
import json
from fang.utils import get_news_label_map
import networkx as nx
from functools import reduce
import operator
from sklearn.preprocessing import MinMaxScaler
import scipy.sparse as sp


class NewsGraph(object):
    def __init__(self, dataset_root, dataset_name, percent):
        self.dataset_root = dataset_root
        self.dataset_name = dataset_name
        self.features = None
        self.labels = None
        self.idx_train = None
        self.idx_val = None
        self.idx_test = None
        self.percent = percent

        self.citation_adj = None
        self.relationship_adj = None
        self.retweet_adj = None
        self.publication_adj = None

        self.report_adj = None
        self.support_adj = None
        self.support_neutral_adj = None
        self.support_negative_adj = None
        self.deny_adj = None
        self.comment_negative_adj = None
        self.comment_neutral_adj = None
        self.unrelated_adj = None

    def is_loaded(self):
        return self.features is not None and self.idx_train \
               is not None and self.idx_val is not None and self.idx_test is not None


    def _random_edge_removal(self, sparse_adj, removal_fraction=0.1):
        """ 随机删除邻接矩阵中指定比例的边 """
        # 将稀疏邻接矩阵转换为COO格式以便于操作非零条目
        adj_coo = sparse_adj.tocoo()
    
    # 获取非零条目的索引，这些条目代表边
        edges = np.array(list(zip(adj_coo.row, adj_coo.col)))
    
    # 计算要删除的边数
        num_edges_to_remove = int(removal_fraction * edges.shape[0])
    
    # 随机选择要删除的边的索引
        np.random.seed(42)  # 为了便于重现性，可以在生产中去掉
        remove_indices = np.random.choice(edges.shape[0], num_edges_to_remove, replace=False)
    
    # 创建一个掩码以保留未标记为删除的边
        remove_mask = np.ones(edges.shape[0], dtype=bool)
        remove_mask[remove_indices] = False
    
    # 使用未被删除的边创建新的边列表
        new_edges = edges[remove_mask]
    
    # 从剩余的边中创建新的稀疏邻接矩阵
        new_sparse_adj = sp.coo_matrix(
        (np.ones(new_edges.shape[0]), (new_edges[:, 0], new_edges[:, 1])),
        shape=sparse_adj.shape,
        dtype=np.float32
        )
    
        return new_sparse_adj


    @staticmethod
    def _encode_one_hot(labels):
        classes = set(labels)
        classes_dict = {c: np.identity(len(classes))[i, :] for i, c in
                        enumerate(classes)}
        labels_one_hot = np.array(list(map(classes_dict.get, labels)),
                                  dtype=np.int32)
        return labels_one_hot

    def _create_binary_symmetric_adj(self, node_name_idx_map, edges_unordered, num_nodes):
        # flatten edge (node name pairs) and look up for node idx
        flattened_mapped_edges = list(map(node_name_idx_map.get, edges_unordered.flatten()))
        flattened_mapped_edges = [c if c is not None else -1 for c in flattened_mapped_edges]
        edges = np.array(flattened_mapped_edges,
                         dtype=np.int32).reshape(edges_unordered.shape)
        edges = edges[(edges >= 0).all(axis=1)]
        sparse_adj = sp.coo_matrix((np.ones(edges.shape[0]), (edges[:, 0], edges[:, 1])),
                                   shape=(num_nodes, num_nodes),
                                   dtype=np.float32)

        # build symmetric adjacency matrix
        symmetric_adj = sparse_adj + sparse_adj.T.multiply(sparse_adj.T > sparse_adj) \
            - sparse_adj.multiply(sparse_adj.T > sparse_adj)

        normalized_adj = self._normalize(symmetric_adj + sp.eye(symmetric_adj.shape[0]))
        return normalized_adj

    def full_epoch_load(self, sparse=False):
        if self.is_loaded():
            return
        print("Load full epoch of {} dataset from {}".format(self.dataset_name, self.dataset_root))

        data_root = os.path.join(self.dataset_root, self.dataset_name)
        entity_path = os.path.join(data_root, "entities.txt")
        entity_feature_path = os.path.join(data_root, "entity_features.tsv")
        source_citation_path = os.path.join(data_root, "source_citation.tsv")
        source_publication_path = os.path.join(data_root, "source_publication.tsv")
        user_relationship_path = os.path.join(data_root, "user_relationships.tsv")
        # user_retweet_path = os.path.join(data_root, "user_retweets.tsv")
        news_info_path = os.path.join(data_root, "news_info.tsv")

        report_stance_path = os.path.join(data_root, "report.tsv")
        support_neutral_stance_path = os.path.join(data_root, "support_neutral.tsv")
        support_negative_stance_path = os.path.join(data_root, "support_negative.tsv")
        deny_stance_path = os.path.join(data_root, "deny.tsv")
        # unrelated_stance_path = os.path.join(data_root, "unrelated.tsv")
        # comment_neutral_stance_path = os.path.join(data_root, "comment_neutral.tsv")
        # comment_negative_stance_path = os.path.join(data_root, "comment_negative.tsv")

        idx_features_labels = np.genfromtxt(entity_feature_path,
                                            dtype=np.dtype(str))
        features = sp.csr_matrix(idx_features_labels[:, 1:], dtype=np.float32)

        # build graph
        entities = utils.load_text_as_list(entity_path)

        # Build list of train/val/test news indices
        news_label_map = get_news_label_map(news_info_path)
        # set a pseudo "true" label for entities that are not news
        news_labels = [news_label_map[e] if e in news_label_map else "real" for i, e in enumerate(entities)]

        idx_train, idx_val, idx_test = [], [], []
        with open(os.path.join(data_root, "train_test_{}.json".format(self.percent)), "rb") as f:
            train_test_val = json.load(f)
        train_news, val_news, test_news = train_test_val["train"], train_test_val["val"], train_test_val["test"]
        for i, e in enumerate(entities):
            if e in news_label_map:
                if e in train_news:
                    idx_train.append(i)
                elif e in val_news:
                    idx_val.append(i)
                elif e in test_news:
                    idx_test.append(i)

        labels = self._encode_one_hot(news_labels)

        print("Train size: {}, test size: {}, validation size: {}".format(len(idx_train), len(idx_val), len(idx_test)))

        num_nodes = len(entities)
        node_names = np.array(entities, dtype=np.dtype(str))
        node_name_idx_map = {j: i for i, j in enumerate(node_names)}
        source_citation_edges_unordered = np.genfromtxt(source_citation_path, dtype=np.dtype(str))
        source_publication_edges_unordered = np.genfromtxt(source_publication_path, dtype=np.dtype(str))
        user_relationship_edges_unordered = np.genfromtxt(user_relationship_path,  dtype=np.dtype(str))
        # user_retweet_edges_unordered = np.genfromtxt(user_retweet_path, dtype=np.dtype(str))

        report_edges_unordered = np.genfromtxt(report_stance_path, dtype=np.dtype(str))
        support_neutral_edges_unordered = np.genfromtxt(support_neutral_stance_path, dtype=np.dtype(str))
        support_negative_edges_unordered = np.genfromtxt(support_negative_stance_path, dtype=np.dtype(str))
        deny_edges_unordered = np.genfromtxt(deny_stance_path, dtype=np.dtype(str))
        # unrelated_edges_unordered = np.genfromtxt(unrelated_stance_path, dtype=np.dtype(str))
        # comment_neutral_edges_unordered = np.genfromtxt(comment_neutral_stance_path, dtype=np.dtype(str))
        # comment_negative_edges_unordered = np.genfromtxt(comment_negative_stance_path, dtype=np.dtype(str))

        self.citation_adj = \
            self._create_binary_symmetric_adj(node_name_idx_map, source_citation_edges_unordered, num_nodes)
        self.relationship_adj = \
            self._create_binary_symmetric_adj(node_name_idx_map, user_relationship_edges_unordered, num_nodes)
        # self.retweet_adj = \
        #     self._create_binary_symmetric_adj(node_name_idx_map, user_retweet_edges_unordered, num_nodes)
        self.publication_adj = \
            self._create_binary_symmetric_adj(node_name_idx_map, source_publication_edges_unordered, num_nodes)

        self.report_adj = self._create_binary_symmetric_adj(node_name_idx_map, report_edges_unordered, num_nodes)
        self.support_neutral_adj = self._create_binary_symmetric_adj(node_name_idx_map, support_neutral_edges_unordered, num_nodes)
        self.support_negative_adj = self._create_binary_symmetric_adj(node_name_idx_map, support_negative_edges_unordered, num_nodes)
        self.deny_adj = self._create_binary_symmetric_adj(node_name_idx_map, deny_edges_unordered, num_nodes)
        # self.comment_negative_adj = self._create_binary_symmetric_adj(node_name_idx_map,
        #                                                               comment_negative_edges_unordered, num_nodes)
        # self.comment_neutral_adj = self._create_binary_symmetric_adj(node_name_idx_map,
        #                                                              comment_neutral_edges_unordered, num_nodes)
        # self.unrelated_adj = self._create_binary_symmetric_adj(node_name_idx_map,
        #                                                         unrelated_edges_unordered, num_nodes)


            # 应用随机边删除
        self.citation_adj = self._random_edge_removal(self.citation_adj, removal_fraction=0.1)
        self.relationship_adj = self._random_edge_removal(self.relationship_adj, removal_fraction=0.1)
        self.publication_adj = self._random_edge_removal(self.publication_adj, removal_fraction=0.1)

        self.report_adj = self._random_edge_removal(self.report_adj, removal_fraction=0.1)
        self.support_neutral_adj = self._random_edge_removal(self.support_neutral_adj, removal_fraction=0.1)
        self.support_negative_adj = self._random_edge_removal(self.support_negative_adj, removal_fraction=0.1)
        self.deny_adj = self._random_edge_removal(self.deny_adj, removal_fraction=0.1)


        print("new code here")
        ################## new #################
        # 1. 合并所有的邻接矩阵，形成一个综合的邻接矩阵
        adjacency_matrices = [
        self.citation_adj,
        self.relationship_adj,
        self.publication_adj,
        self.report_adj,
        self.support_neutral_adj,
        self.support_negative_adj,
        self.deny_adj
        ]

        # 将所有的邻接矩阵相加
        combined_adj = reduce(operator.add, adjacency_matrices)

        # 确保综合的邻接矩阵是对称的
        combined_adj = combined_adj + combined_adj.T.multiply(combined_adj.T > combined_adj) - combined_adj.multiply(combined_adj.T > combined_adj)
        print("new code check point 1")
        # 2. 使用 networkx 从综合邻接矩阵创建图
        #G = nx.from_scipy_sparse_array(combined_adj)
        
        # 3. 计算节点的度
        #degrees = np.array([val for (node, val) in G.degree()]).reshape(-1, 1)
        degrees = np.array(combined_adj.sum(axis=1)).flatten().reshape(-1, 1)

        # 4. 计算节点的聚类系数
        #clustering_coeffs_dict = nx.clustering(G)
        #clustering_coeffs = np.array([clustering_coeffs_dict[i] for i in range(num_nodes)]).reshape(-1, 1)

        print("new code check point 2")

        # 6. 将新的特征与原有的特征进行拼接
        additional_features = degrees

        # 将现有的特征转换为密集的 numpy 数组
        features_dense = np.array(features.todense())

        # 拼接新的特征
        features_with_additional = np.hstack([features_dense, additional_features])

        # 7. 将扩展后的特征矩阵赋值给 self.features
        self.features = torch.FloatTensor(features_with_additional)



        #normalized_features = self._normalize(features)
        #self.features = torch.FloatTensor(np.array(normalized_features.todense()))
        
        self.labels = torch.LongTensor(np.where(labels)[1])

        if sparse:
            self.citation_adj = self._sparse_mx_to_torch_sparse_tensor(self.citation_adj)
            self.relationship_adj = self._sparse_mx_to_torch_sparse_tensor(self.relationship_adj)
            # self.retweet_adj = self._sparse_mx_to_torch_sparse_tensor(self.retweet_adj)
            self.publication_adj = self._sparse_mx_to_torch_sparse_tensor(self.publication_adj)

            self.report_adj = self._sparse_mx_to_torch_sparse_tensor(self.report_adj)
            self.support_neutral_adj = self._sparse_mx_to_torch_sparse_tensor(self.support_neutral_adj)
            self.support_negative_adj = self._sparse_mx_to_torch_sparse_tensor(self.support_negative_adj)
            # self.support_adj = self._sparse_mx_to_torch_sparse_tensor(self.support_adj)
            self.deny_adj = self._sparse_mx_to_torch_sparse_tensor(self.deny_adj)
            # self.comment_negative_adj = self._sparse_mx_to_torch_sparse_tensor(self.comment_negative_adj)
            # self.comment_neutral_adj = self._sparse_mx_to_torch_sparse_tensor(self.comment_neutral_adj)
            # self.unrelated_adj = self._sparse_mx_to_torch_sparse_tensor(self.unrelated_adj)
        else:
            self.citation_adj = torch.FloatTensor(np.array(self.citation_adj.todense()))
            self.relationship_adj = torch.FloatTensor(np.array(self.relationship_adj.todense()))
            # self.retweet_adj = torch.FloatTensor(np.array(self.retweet_adj.todense()))
            self.publication_adj = torch.FloatTensor(np.array(self.publication_adj.todense()))

            self.report_adj = torch.FloatTensor(np.array(self.report_adj.todense()))
            self.support_neutral_adj = torch.FloatTensor(np.array(self.support_adj.todense()))
            self.support_negative_adj = torch.FloatTensor(np.array(self.support_adj.todense()))
            self.deny_adj = torch.FloatTensor(np.array(self.deny_adj.todense()))
            # self.comment_negative_adj = torch.FloatTensor(np.array(self.comment_negative_adj.todense()))
            # self.comment_neutral_adj = torch.FloatTensor(np.array(self.comment_neutral_adj.todense()))
            # self.unrelated_adj = torch.FloatTensor(np.array(self.unrelated_adj.todense()))

        self.idx_train = torch.LongTensor(idx_train)
        self.idx_val = torch.LongTensor(idx_val)
        self.idx_test = torch.LongTensor(idx_test)

    @staticmethod
    def _normalize(mtx):
        """Row-normalize sparse matrix"""
        row_sum = np.array(mtx.sum(axis=1))
        sum_inv = np.power(row_sum, -1).flatten()
        sum_inv[np.isinf(sum_inv)] = 0
        row_mtx_inv = sp.diags(sum_inv)
        return row_mtx_inv.dot(mtx)

    @staticmethod
    def _sparse_mx_to_torch_sparse_tensor(sparse_mx):
        """Convert a scipy sparse matrix to a torch sparse tensor."""
        sparse_mx = sparse_mx.tocoo().astype(np.float32)
        indices = torch.from_numpy(
            np.vstack((sparse_mx.row, sparse_mx.col)).astype(np.int64))
        values = torch.from_numpy(sparse_mx.data)
        shape = torch.Size(sparse_mx.shape)
        return torch.sparse.FloatTensor(indices, values, shape)