import math

import torch
from torch import nn
from torch.nn import functional as F
from torchvision.models import resnet50, ResNet50_Weights

# from .ehr_transformer import EHRTransformer
import torch
from torch import nn


class LearnablePositionalEncoding(nn.Module):
    def __init__(self, d_model: int, dropout: float = 0.1, max_len: int = 500):
        super().__init__()
        self.dropout = nn.Dropout(p=dropout)

        self.pe = nn.Parameter(torch.rand(1, max_len, d_model))
        self.pe.data.uniform_(-0.1, 0.1)

    def forward(self, x):
        x = x + self.pe[:, :x.size(1)]  # x: (batch_size, seq_len, embedding_dim)
        return self.dropout(x)


class EHRTransformer(nn.Module):
    #=============change max_len从350改为4-96
    def __init__(self, input_size, num_classes,
                 d_model=256, n_head=8, n_layers_feat=1,
                 n_layers_shared=1, n_layers_distinct=1,
                 dropout=0.3, max_len=4097):
        super().__init__()
        self.d_model = d_model
        self.max_len = max_len

        self.emb = nn.Linear(input_size, d_model)
        self.pos_encoder = LearnablePositionalEncoding(d_model, dropout=0, max_len=max_len)

        layer = nn.TransformerEncoderLayer(d_model=d_model, nhead=n_head, batch_first=True, dropout=dropout)
        self.model_feat = nn.TransformerEncoder(layer, num_layers=n_layers_feat)

        layer = nn.TransformerEncoderLayer(d_model=d_model, nhead=n_head, batch_first=True, dropout=dropout)
        self.model_shared = nn.TransformerEncoder(layer, num_layers=n_layers_shared)

        layer = nn.TransformerEncoderLayer(d_model=d_model, nhead=n_head, batch_first=True, dropout=dropout)
        self.model_distinct = nn.TransformerEncoder(layer, num_layers=n_layers_distinct)
        self.fc_distinct = nn.Linear(d_model, num_classes)

    def forward(self, x, seq_lengths):
        # attn_mask = torch.stack([torch.cat([torch.zeros(len_, device=x.device),
        #                          float('-inf')*torch.ones(max(seq_lengths)-len_, device=x.device)])
        #                         for len_ in seq_lengths])
        # print(f'ecg transform inout ecg {x.shape}')#[16,12,4096]
        x=x.permute(0,2,1)
        # print(f'after permute ecg {x.shape}')

        x = self.emb(x) # [16,4096,12]
        x = self.pos_encoder(x)
#=======删除atten_mask
        # feat = self.model_feat(x, src_key_padding_mask=attn_mask)
        # h_shared = self.model_shared(feat, src_key_padding_mask=attn_mask)
        # h_distinct = self.model_distinct(feat, src_key_padding_mask=attn_mask)

        # padding_mask = torch.ones_like(attn_mask).unsqueeze(2)
        # padding_mask[attn_mask==float('-inf')] = 0
#=======删除atten_mask
        feat = self.model_feat(x)

        h_shared = self.model_shared(feat)
        h_distinct = self.model_distinct(feat)
#=======删除atten_mask
        # padding_mask = torch.ones_like(attn_mask).unsqueeze(2)
        # padding_mask[attn_mask==float('-inf')] = 0
        # rep_shared = (padding_mask * h_shared).sum(dim=1) / padding_mask.sum(dim=1)
        # rep_distinct = (padding_mask * h_distinct).sum(dim=1) / padding_mask.sum(dim=1)
#=======删除atten_mask

#======A=====================================
#         rep_shared = h_shared.mean(dim=1)  # 对时间步求均值，结果形状为 (batch_size, d_model)
#         rep_distinct = h_distinct.mean(dim=1)  # 对时间步求均值，结果形状为 (batch_size, d_model)
# #======A=====================================
    #    TODO:OR都是均值应该差不多，不过需要确定是在哪个维度求均值
#======B=====================================
        rep_shared = h_shared.sum(dim=1) / h_shared.shape[1]  # 对时间步求均值
        rep_distinct = h_distinct.sum(dim=1) / h_distinct.shape[1]  # 对时间步求均值
#======B=====================================
        # 生成预测
        pred_distinct = self.fc_distinct(rep_distinct).sigmoid()  # 使用Sigmoid激活函数


        # pred_distinct = self.fc_distinct(rep_distinct).sigmoid()

        return rep_shared, rep_distinct, pred_distinct



class DrFuseModel(nn.Module):
    def __init__(self, hidden_size, num_classes, ehr_dropout, ehr_n_layers, ehr_n_head,
                 cxr_model='swin_s', logit_average=False):
        super().__init__()
        self.num_classes = num_classes
        self.logit_average = logit_average
        #===============inputsize从76改为12
        self.ehr_model = EHRTransformer(input_size=12, num_classes=num_classes,
                                        d_model=hidden_size, n_head=ehr_n_head,
                                        n_layers_feat=1, n_layers_shared=ehr_n_layers,
                                        n_layers_distinct=ehr_n_layers,
                                        dropout=ehr_dropout)

        resnet = resnet50()
        self.cxr_model_feat = nn.Sequential(
            resnet.conv1,
            resnet.bn1,
            resnet.relu,
            resnet.maxpool,
        )

        resnet = resnet50()
        self.cxr_model_shared = nn.Sequential(
            resnet.layer1,
            resnet.layer2,
            resnet.layer3,
            resnet.layer4,
            resnet.avgpool,
            nn.Flatten(),
        )
        self.cxr_model_shared.fc = nn.Linear(in_features=resnet.fc.in_features, out_features=hidden_size)

        resnet = resnet50()
        self.cxr_model_spec = nn.Sequential(
            resnet.layer1,
            resnet.layer2,
            resnet.layer3,
            resnet.layer4,
            resnet.avgpool,
            nn.Flatten(),
        )
        self.cxr_model_spec.fc = nn.Linear(in_features=resnet.fc.in_features, out_features=hidden_size)

        self.shared_project = nn.Sequential(
            nn.Linear(hidden_size, hidden_size*2),
            nn.ReLU(),
            nn.Linear(hidden_size*2, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, hidden_size)
        )

        self.ehr_model_linear = nn.Linear(in_features=hidden_size, out_features=num_classes)
        self.cxr_model_linear = nn.Linear(in_features=hidden_size, out_features=num_classes)
        self.fuse_model_shared = nn.Linear(in_features=hidden_size, out_features=num_classes)

        self.domain_classifier = nn.Sequential(
            nn.Linear(hidden_size, hidden_size//2),
            nn.ReLU(),
            nn.Linear(hidden_size//2, 1)
        )
        self.attn_proj = nn.Linear(hidden_size, (2+num_classes)*hidden_size)
        self.final_pred_fc = nn.Linear(hidden_size, num_classes)

    def forward(self, x, img, seq_lengths, pairs):

        # x=x.permute(0,2,1)
        # print(f'after permute ecg {x.shape}')
        
        feat_ehr_shared, feat_ehr_distinct, pred_ehr = self.ehr_model(x, seq_lengths)
        feat_cxr = self.cxr_model_feat(img)
        feat_cxr_shared = self.cxr_model_shared(feat_cxr)
        feat_cxr_distinct = self.cxr_model_spec(feat_cxr)

        # get shared feature
        pred_cxr = self.cxr_model_linear(feat_cxr_distinct).sigmoid()

        feat_ehr_shared = self.shared_project(feat_ehr_shared)
        feat_cxr_shared = self.shared_project(feat_cxr_shared)
        # pairs = torch.FloatTensor(pairs)
        pairs = pairs.unsqueeze(1)
        # print(f' after unqueeze pairs  {pairs}')
        h1 = feat_ehr_shared
        h2 = feat_cxr_shared
        term1 = torch.stack([h1+h2, h1+h2, h1, h2], dim=2)
        term2 = torch.stack([torch.zeros_like(h1), torch.zeros_like(h1), h1, h2], dim=2)
        feat_avg_shared = torch.logsumexp(term1, dim=2) - torch.logsumexp(term2, dim=2)
        # print(f'pairs {pairs.shape}')
        # print(f'feat_avg_shared {feat_avg_shared.shape}')
        feat_avg_shared = pairs * feat_avg_shared + (1 - pairs) * feat_ehr_shared
        pred_shared = self.fuse_model_shared(feat_avg_shared).sigmoid()

        # Disease-wise Attention
        attn_input = torch.stack([feat_ehr_distinct, feat_avg_shared, feat_cxr_distinct], dim=1)
        qkvs = self.attn_proj(attn_input)
        q, v, *k = qkvs.chunk(2+self.num_classes, dim=-1)

        # compute query vector
        q_mean = pairs * q.mean(dim=1) + (1-pairs) * q[:, :-1].mean(dim=1)

        # compute attention weighting
        ks = torch.stack(k, dim=1)
        attn_logits = torch.einsum('bd,bnkd->bnk', q_mean, ks)
        attn_logits = attn_logits / math.sqrt(q.shape[-1])

        # filter out non-paired
        attn_mask = torch.ones_like(attn_logits)
        # print(f'pairs in drfuse model {pairs}')
        # if pairs.squeeze()==0:
        if (pairs.squeeze()).any()==0:
            print(f'pairs.squeeze()==0')
        attn_mask[pairs.squeeze()==0, :, -1] = 0
        
        attn_logits = attn_logits.masked_fill(attn_mask == 0, float('-inf'))
        attn_weights = F.softmax(attn_logits, dim=-1)

        # get final class-specific representation and prediction
        feat_final = torch.matmul(attn_weights, v)
        pred_final = self.final_pred_fc(feat_final)

        pred_final = torch.diagonal(pred_final, dim1=1, dim2=2).sigmoid()
        # pred_final = torch.diagonal(pred_final, dim1=1, dim2=2)

        outputs = {
            'feat_ehr_shared': feat_ehr_shared,
            'feat_cxr_shared': feat_cxr_shared,
            'feat_ehr_distinct': feat_ehr_distinct,
            'feat_cxr_distinct': feat_cxr_distinct,
            'feat_final': feat_final,
            'pred_final': pred_final,
            'pred_shared': pred_shared,
            'pred_ehr': pred_ehr,
            'pred_cxr': pred_cxr,
            'attn_weights': attn_weights,
        }

        return outputs

    # Create a model instance
# import torch
# from torchinfo import summary
# model = DrFuseModel(hidden_size=512, num_classes=7, ehr_dropout=0.1, ehr_n_layers=3, ehr_n_head=8)

# # Create dummy input tensors
# x = torch.randn(16, 4096, 12)  # EHR input
# img = torch.randn(16, 3, 224, 224)  # CXR input
# # seq_lengths = torch.randint(1, 4096, (16,))  # Sequence lengths
# # pairs = torch.randn(16, 10)  # Example pairs input
# seq_lengths=4096
# # seq_lengths = torch.full((16,), 4096) 
# # pairs=16
# pairs = [True, True, True, True, True, True, True, True, True, True, True, True, True, True, True, True]
# # pairs = torch.tensor(pairs, dtype=torch.bool)  # Convert to a PyTorch tensor
# pairs = torch.FloatTensor(pairs)
# # Forward pass
# model(x, img, seq_lengths, pairs)
# summary(model, input_data=(x, img, seq_lengths, pairs))


# model = EHRTransformer(input_size=12, num_classes=7, d_model=256)

# # 创建随机输入张量
# batch_size = 16
# channels = 12
# timestamps = 4096

# # EHR input shape: (batch_size, channels, timestamps)
# x = torch.randn(batch_size, timestamps, channels)  # 随机生成 EHR 数据
# seq_lengths = torch.full((batch_size,), timestamps)  # 假设所有样本的长度都是 4096

# # 前向传播并打印输出形状
# model(x, seq_lengths)
# summary(model, input_data=(x, seq_lengths))