import os
import time
import random
import pickle
import numpy as np
from math import sqrt
from scipy.stats import hypergeom
import pandas as pd
import networkx as nx
import sys
from scipy.sparse import coo_matrix, csr_matrix, diags


start_index=int(sys.argv[1])
step=int(sys.argv[2])
connectivity_p_cutoff=float(sys.argv[3])
is_smoothing=sys.argv[4]
DIR_MODULE=sys.argv[5]
DIR_DATA=sys.argv[6]
DIR_NET=sys.argv[7]

if not os.path.exists(DIR_MODULE):
    try:
        os.makedirs(DIR_MODULE)
    except FileExistsError:
        # Directory already exists
        pass

def largest_component(G):
    # Convert the edge list to a NetworkX graph
    G_nx = nx.Graph()
    G_nx.add_edges_from(G)

    # Find the largest connected component
    largest_cc = max(nx.connected_components(G_nx), key=len)
    subgraph = G_nx.subgraph(largest_cc).copy()

    # Convert the subgraph back to an edge list
    largest_component_edges = np.array(subgraph.edges())

    return largest_component_edges, len(largest_cc)

def network_smoothing(mutation,G,flag=1,alpha=0.5):
    mutation = mutation['score'] / np.sum(mutation['score']) * len(mutation)
    F = mutation
    Y = mutation
    sn = np.array(G.sum(axis=1)).flatten()#row sum
    D = diags(1 / sn)
    W1 = G.dot(D)  # md
    delta = 100
    k = 0
    x = F

    if flag == 1:  # diffusion
        while delta > 10 ** (-8):
            F0 = F
            F = (1 - alpha) * W1.dot(F0) + alpha * Y
            delta = np.sum((F - F0) ** 2)
    elif flag == 2:  # conduction
        F = F.T
        Y = Y.T
        while delta > 10 ** (-8):
            F0 = F
            F = (1 - alpha) * F0.dot(W1) + alpha * Y
            delta = np.sum((F - F0) ** 2)
        F = F.T
    elif flag == 3:
        W1 = G
        a, b = np.where(W1 != 0)
        for i in range(len(a)):
            W1[a[i], b[i]] = 1 / np.sqrt(sn[a[i]] * sn[b[i]])
        while delta > 10 ** (-8):
            F0 = F
            F = (1 - alpha) * W1.dot(F0) + alpha * Y
            delta = np.sum((F - F0) ** 2)
    F = F * np.sum(mutation) / len(mutation)
    return F


# ------------- Prepare Input ------------------------------------
## Prepare input_score.txt: column 1: protein entrez id, column 2: node score -- in our case, |log2FC|
node_score_df=pd.read_csv(DIR_DATA)
node_score_df=node_score_df[['node_id','Beta']].rename(columns={'node_id':'id','Beta':'score'})
node_score_df=node_score_df.dropna()
node_score_df.sort_values(by='id',inplace=True)
node_score_df.iloc[:, 1] = node_score_df.iloc[:, 1].astype(np.float64)

# Prepare input ppi.p
net_df = pd.read_csv(DIR_NET)

Net = [tuple(x) for x in net_df.values]
largest_component_edges, size = largest_component(Net)
Net=largest_component_edges
NET_IDS=np.unique(Net)

NODE_NEIGHBOR = {}
for row in Net:
    protein1 = row[0]
    protein2 = row[1]
    if protein1 not in NODE_NEIGHBOR:
        NODE_NEIGHBOR[protein1] = set()
    NODE_NEIGHBOR[protein1].add(protein2)
    if protein2 not in NODE_NEIGHBOR:
        NODE_NEIGHBOR[protein2] = set()
    NODE_NEIGHBOR[protein2].add(protein1)
for protein in NODE_NEIGHBOR:
    NODE_NEIGHBOR[protein] = list(NODE_NEIGHBOR[protein])

MG = pd.DataFrame({'id': NET_IDS})#mutation graph
MG = MG.merge(node_score_df, on='id', how='left')
MG['score'] = MG['score'].fillna(0)# input scores for all PPI ids (if no, 0)


if is_smoothing:
    PM = MG
    G = np.searchsorted(MG['id'], Net.flatten()).reshape(Net.shape)# index of Net ids in the MG
    rows, cols = np.hstack([G[:, 0], G[:, 1]]), np.hstack([G[:, 1], G[:, 0]])
    G = coo_matrix((np.ones_like(rows), (rows, cols)), shape=(len(MG), len(MG)))
    F=network_smoothing(PM,G,1,0.5)
    PM['score']=F#replace score with smoothing score
    MG = PM

NODE_ID=MG['id'].to_numpy()
NODE_SCORE=MG['score'].to_numpy()
NODE_DEGREE = np.array([len(NODE_NEIGHBOR[node]) for node in NODE_ID])  # Array: pos -> degree
N_NODES = len(NODE_ID)
MEAN_NODE_SCORE = NODE_SCORE.mean()
ID2POS = {j: i for i, j in enumerate(NODE_ID)}  # {id -> pos}
NODE_ID_SET = set(NODE_ID)

def P_Connectivity(k_m, k_i, n):
    p_rv = hypergeom(N_NODES, n, k_i)
    return p_rv.cdf(min(k_i, n)) - p_rv.cdf(k_m - 1)


def FindModule(idx):
    random.seed(idx)

    last_added_pos = random.randrange(0, N_NODES)
    module = [last_added_pos]  # [pos1, pos2, ...]
    module_id = {NODE_ID[last_added_pos]}  # {id1, id2, ...}
    score_sum = 0
    neighbors_id = set()


    while True:
        print(".", end="")

        # -------- add note here -------- #
        module_size = len(module)
        if is_smoothing:#limit size if smoothing
            if module_size>min(np.random.lognormal(3, 0.5),200):
                break
        score_sum += NODE_SCORE[last_added_pos]
        module_score = (score_sum - module_size * MEAN_NODE_SCORE) / sqrt(module_size)

        neighbors_id |= set(NODE_NEIGHBOR[NODE_ID[last_added_pos]])#MZ changed
        neighbors_id -= module_id
        if not len(neighbors_id):
            break
        neighbors_pos = np.array([ID2POS[id] for id in sorted(neighbors_id)])  ###

        # -------- select nodes that could increase module score -------- #
        qualified_pos = neighbors_pos[module_score * sqrt(module_size) + NODE_SCORE[neighbors_pos] > module_score * sqrt(module_size + 1) + MEAN_NODE_SCORE]
        if not len(qualified_pos):
            break

        # -------- select nodes whose connectivity significance p-value <= connectivity_p_cutoff -------- #
        k_m_ls = [len(set(NODE_NEIGHBOR[NODE_ID[pos]]) & module_id) for pos in qualified_pos]# MZ changed
        k_i_ls = NODE_DEGREE[qualified_pos]
        qualified_csp_pos = [pos for pos, k_m, k_i in zip(qualified_pos, k_m_ls, k_i_ls) if P_Connectivity(k_m, k_i, module_size) <= connectivity_p_cutoff]
        if not len(qualified_csp_pos):
            break

        # -------- add note here -------- #
        qualified_csp_score = NODE_SCORE[qualified_csp_pos]
        max_pos_index = np.where((qualified_csp_score == np.max(qualified_csp_score)))[0]
        last_added_pos = qualified_csp_pos[random.choice(max_pos_index)]
        module.append(last_added_pos)
        module_id.add(NODE_ID[last_added_pos])

    return NODE_ID[module], module_score


# ----------------------------------------------------------
module_dict={}
score_dict={}
for idx in range(0, step):
    start_time = time.time()
    one_module,one_score=FindModule(idx+start_index)
    module_dict[idx+start_index]=one_module
    score_dict[idx+start_index]=one_score
    print("--- Experiment %s takes %.1f seconds ---" % (idx, time.time() - start_time))

with open(DIR_MODULE+'module_'+str(start_index)+'.pkl', 'wb') as f:
    pickle.dump(module_dict, f)

with open(DIR_MODULE+'score_'+str(start_index)+'.pkl', 'wb') as f:
    pickle.dump(score_dict, f)

