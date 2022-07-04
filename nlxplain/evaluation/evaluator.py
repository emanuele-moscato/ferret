from audioop import reverse
import copy
import torch
import numpy as np
import pandas as pd
import scipy.stats as stats
from sklearn.metrics import auc, precision_recall_curve


# As in Attention is not explanation
# https://github.com/successar/AttentionExplanation/blob/425a89a49a8b3bffc3f5e8338287e2ecd0cf1fa2/common_code/kendall_top_k.py


def color_nan_black(val):
    """Color the nan text black"""
    if np.isnan(val):
        return "color: black"


def color_nan_white_background(val):
    """Color the nan cell background white"""
    if np.isnan(val):
        return "background-color: white"


def kendalltau_distance(x, y):
    if x.size != y.size:
        raise NameError("The two arrays need to have same lengths")
    return 1 - (stats.kendalltau(x, y)[0] / 2 + 0.5)


class Evalutator:
    def __init__(self, explanator):
        # I still use the explainer object as an entry point.
        # Maybe it is better to merge it
        self.explanator = explanator

    def _get_id_tokens_greater_th(self, soft_score_explanation, th, only_pos=None):
        id_top = np.where(soft_score_explanation > th)[0]
        return id_top

    def _get_id_tokens_top_k(self, soft_score_explanation, k, only_pos=True):
        if only_pos:
            id_top_k = [
                i
                for i in np.array(soft_score_explanation).argsort()[-k:][::-1]
                if soft_score_explanation[i] > 0
            ]
        else:
            id_top_k = np.array(soft_score_explanation).argsort()[-k:][::-1]
        if len(id_top_k) != k:
            return None
        return id_top_k

    def _get_id_tokens_percentage(
        self, soft_score_explanation, percentage, only_pos=True
    ):
        v = int(percentage * len(soft_score_explanation))
        # Only if we remove at least instance. TBD
        if v > 0 and v < len(soft_score_explanation):
            return self._get_id_tokens_top_k(
                soft_score_explanation, v, only_pos=only_pos
            )
        else:
            return None

    def _compute_aopc(self, scores):
        # tmp
        from statistics import mean

        return {
            ex: mean([s[1] for s in v.values()]) if v is not None else np.nan
            for ex, v in scores.items()
        }

    from typing import List

    def get_true_rational_tokens(
        self, original_tokens: List[str], rationale_original_tokens: List[int]
    ) -> List[int]:
        # original_tokens --> list of words.
        # rationale_original_tokens --> 0 or 1, if the token belongs to the rationale or not
        # Typically, the importance is associated with each word rather than each token.
        # We convert each word in token using the tokenizer. If a word is in the rationale,
        # we consider as important all the tokens of the word.
        token_rationale = []
        for t, rationale_t in zip(original_tokens, rationale_original_tokens):
            converted_token = self.explanator.tokenizer.encode(t)[1:-1]

            for token_i in converted_token:
                token_rationale.append(rationale_t)
        return token_rationale

    def _check_and_define_get_id_function(self, based_on):
        if based_on == "th":
            get_discrete_rationale_function = self._get_id_tokens_greater_th
        elif based_on == "k":
            get_discrete_rationale_function = self._get_id_tokens_top_k
        elif based_on == "perc":
            get_discrete_rationale_function = self._get_id_tokens_percentage
        else:
            raise ValueError(f"{based_on} type not supported. Specify th, k or perc.")
        return get_discrete_rationale_function

    def compute_comprehensiveness_ths(
        self,
        text,
        soft_score_explanation,
        thresholds,
        based_on="th",
        target=1,
        only_pos=True,
        remove_first_last=True,
        remove_tokens=True,
    ):

        # Get token ids of text. If remove_first_last, first and last token id (CLS and ) are removed
        item = self.explanator._get_item(text)

        # Get token ids of the sentence
        input_len = item["attention_mask"].sum().item()
        input_ids = item["input_ids"][0][:input_len].tolist()

        if remove_first_last == True:
            input_ids = input_ids[1:-1]

        # Get prediction probability of the input sencence for the target
        outputs = self.explanator._forward(text)
        logits = outputs.logits[0]
        baseline = logits.softmax(-1)[target].item()

        discrete_expl_ths = []
        id_tops = []

        get_discrete_rationale_function = self._check_and_define_get_id_function(
            based_on
        )

        for v in thresholds:

            # Get rationale
            id_top = get_discrete_rationale_function(
                soft_score_explanation, v, only_pos
            )
            id_tops.append(id_top)

            if id_top is None:
                continue

            sample = np.array(copy.copy(input_ids))

            if remove_tokens:
                discrete_expl_th_token_ids = np.delete(sample, id_top)
            else:
                sample[id_top] = self.explanator.tokenizer.mask_token_id
                discrete_expl_th_token_ids = sample

            discrete_expl_th = self.explanator.tokenizer.decode(
                discrete_expl_th_token_ids
            )

            discrete_expl_ths.append(discrete_expl_th)

        if discrete_expl_ths == []:
            return None

        # Prediction probability for the target
        inputs = self.explanator.tokenizer(
            discrete_expl_ths, return_tensors="pt", padding="longest"
        )

        with torch.no_grad():
            outputs = self.explanator.model(**inputs)

        logits = outputs.logits.softmax(-1)[:, target]

        # Compute probability difference
        removal_importance = baseline - logits

        result = {}
        r = 0
        for i in range(len(thresholds)):
            if id_tops[i] is not None:
                result[thresholds[i]] = (id_tops[i], removal_importance[r].item())
                r += 1
        return result

    def compute_sufficiency_ths(
        self,
        text,
        soft_score_explanation,
        thresholds,
        based_on="th",
        target=1,
        only_pos=True,
        remove_first_last=True,
        remove_tokens=True,
    ):

        # Get token ids of text. If remove_first_last, first and last token id (CLS and ) are removed
        item = self.explanator._get_item(text)

        # Get token ids of the sentence
        input_len = item["attention_mask"].sum().item()
        input_ids = item["input_ids"][0][:input_len].tolist()

        if remove_first_last == True:
            input_ids = input_ids[1:-1]

        # Get prediction probability of the input sencence for the target
        outputs = self.explanator._forward(text)
        logits = outputs.logits[0]
        baseline = logits.softmax(-1)[target].item()

        discrete_expl_ths = []
        id_tops = []

        get_discrete_rationale_function = self._check_and_define_get_id_function(
            based_on
        )

        for v in thresholds:

            # Get rationale
            id_top = get_discrete_rationale_function(
                soft_score_explanation, v, only_pos
            )
            id_tops.append(id_top)

            if id_top is None:
                continue

            sample = np.array(copy.copy(input_ids))

            if remove_tokens:
                discrete_expl_th_token_ids = sample[id_top]
            else:
                mask = np.ones(len(sample), dtype=bool)
                mask[id_top] = False
                sample[mask] = self.explanator.tokenizer.mask_token_id
                discrete_expl_th_token_ids = sample

            discrete_expl_th = self.explanator.tokenizer.decode(
                discrete_expl_th_token_ids
            )

            discrete_expl_ths.append(discrete_expl_th)

        if discrete_expl_ths == []:
            return None

        # Prediction probability for the target
        inputs = self.explanator.tokenizer(
            discrete_expl_ths, return_tensors="pt", padding="longest"
        )

        with torch.no_grad():
            outputs = self.explanator.model(**inputs)

        logits = outputs.logits.softmax(-1)[:, target]

        # Compute probability difference
        removal_importance = baseline - logits

        result = {}
        r = 0
        for i in range(len(thresholds)):
            if id_tops[i] is not None:
                result[thresholds[i]] = (id_tops[i], removal_importance[r].item())
                r += 1
        return result

    def _compute_auprc_soft_scoring(self, true_rationale, soft_scores, only_pos=True):
        if only_pos:
            # Only positive terms of explanations.
            # https://github.com/hate-alert/HateXplain/blob/daa7955afbe796b00e79817f16621469a38820e0/testing_with_lime.py#L276
            soft_scores = [v if v > 0 else 0 for v in soft_scores]
        # true_rationale = [int(t) for t in true_rationale]
        precision, recall, _ = precision_recall_curve(true_rationale, soft_scores)
        auc_score = auc(recall, precision)
        return auc_score

    def evaluate_explainers(
        self,
        text,
        explanations,
        thresholds,
        true_rationale=None,
        based_on="th",
        show_all_th=False,
        rank_explainer=True,
        show_i=0,
        style_df=True,
        target=1,
        underline_rationale=False,
        **kwargs,
    ):

        compr = {}
        suff = {}
        kendall_distances = {}
        auprc_soft_plausibility = {}

        df_eval = copy.deepcopy(explanations)

        if sum(explanations.columns.duplicated().astype(int)) > 0:
            df_eval.columns = pd.io.parsers.base_parser.ParserBase(
                {"names": explanations.columns, "usecols": None}
            )._maybe_dedup_names(explanations.columns)

        text_tokens = list(df_eval.columns)

        occl_importance = (
            self.explanator.compute_occlusion_importance(text, target=target).numpy()
            * -1
        )

        for explainer_type in explanations.index:
            soft_score_explanation = explanations.loc[explainer_type].values

            # Faithfulness - Comprehensiveness
            compr[explainer_type] = self.compute_comprehensiveness_ths(
                text,
                soft_score_explanation,
                thresholds,
                based_on=based_on,
                target=target,
                **kwargs,
            )

            # Faithfulness - Sufficiency
            suff[explainer_type] = self.compute_sufficiency_ths(
                text,
                soft_score_explanation,
                thresholds,
                based_on=based_on,
                target=target,
                **kwargs,
            )

            # Faithfulness - Kendall tau distance w.r.t. leave one out
            kendall_distances[explainer_type] = kendalltau_distance(
                occl_importance, soft_score_explanation
            )

            if true_rationale is not None:
                # We can compute the plausibility metrics.

                # Plausibility - Area Under the Precision- Recall curve (AUPRC) - ERASER

                # TODO.
                # Consider only the positive scores (as in HateXplain)
                only_pos = kwargs["only_pos"] if "only_pos" in kwargs else True
                auprc_soft_plausibility[
                    explainer_type
                ] = self._compute_auprc_soft_scoring(
                    true_rationale, soft_score_explanation, only_pos=only_pos
                )
        df_eval["taud_loo"] = [kendall_distances[e] for e in df_eval.index]
        if true_rationale is not None:

            df_eval["auprc_plau"] = [auprc_soft_plausibility[e] for e in df_eval.index]

        if len(thresholds) > 1:

            aopc_comprehensiveness = self._compute_aopc(compr)
            aopc_sufficiency = self._compute_aopc(suff)

            df_eval["aopc_compr"] = [aopc_comprehensiveness[e] for e in df_eval.index]
            df_eval["aopc_suff"] = [aopc_sufficiency[e] for e in df_eval.index]

        if show_all_th:

            def _get_score(d, e, th):
                def_v = np.nan
                if d is None or e not in d or d[e] is None or th not in d[e]:
                    return np.nan
                return d[e][th][1]

            for th in thresholds:
                if compr is not None:
                    df_eval[f"compr_{th}"] = [
                        _get_score(compr, e, th) for e in df_eval.index
                    ]
                if suff:
                    df_eval[f"suff_{th}"] = [
                        _get_score(suff, e, th) for e in df_eval.index
                    ]
        df_style = None

        if rank_explainer:
            df_eval = self._rank_explainers(df_eval, text_tokens, thresholds[show_i])

        if style_df:
            df_style = self._style_result(
                df_eval,
                thresholds[show_i],
                text_tokens,
                compr,
                underline_rationale=underline_rationale,
            )

        return df_eval, df_style

    # Temporary
    def _style_result(
        self, df_eval, th, score_cols, d_indexes, underline_rationale=False
    ):
        import seaborn as sns

        def undeline_rationale(x, d, th):
            if d is None or x.name not in d or d[x.name] is None:
                return "background: white"
            return [
                "background: #cbf3d2"
                if th in d[x.name] and e in d[x.name][th][0]
                else "background: white"
                for e, v in enumerate(x)
            ]

        if underline_rationale:
            df_st = df_eval.style.apply(
                lambda x: undeline_rationale(x, d_indexes, th),
                axis=1,
                subset=score_cols,
            )
        else:

            palette = sns.diverging_palette(240, 10, as_cmap=True)
            df_st = df_eval.style.background_gradient(
                axis=1, cmap=palette, vmin=-1, vmax=1
            )

        # Higher is better
        show_higher_cols = [f"compr_{th}", "aopc_compr", "auprc_plau"]
        show_higher_cols = [i for i in show_higher_cols if i in df_eval.columns]
        palette = sns.diverging_palette(150, 275, s=80, l=55, n=9, as_cmap=True)
        df_st.background_gradient(
            axis=1, cmap=palette, vmin=-1, vmax=1, subset=show_higher_cols
        )

        # Close to 0 is better
        show_lower_cols = [f"suff_{th}", "aopc_suff", "taud_loo"]
        [f"{v}_r" for v in show_lower_cols]
        show_lower_cols = [i for i in show_lower_cols if i in df_eval.columns]

        palette = sns.light_palette("blue", as_cmap=True, reverse=True)
        df_st.background_gradient(
            axis=1, cmap=palette, vmin=-1, vmax=1, subset=show_lower_cols
        )

        df_st.background_gradient(
            axis=1,
            cmap=sns.light_palette("lightblue", as_cmap=True, reverse=True),
            vmin=1,
            vmax=len(df_eval),
            subset=[
                f"{v}_r"
                for v in show_higher_cols + show_lower_cols
                if f"{v}_r" in df_eval.columns
            ],
        )
        df_st.applymap(lambda x: color_nan_black(x)).applymap(
            lambda x: color_nan_white_background(x)
        )
        return df_st

    def _rank_explainers(self, df_eval, score_cols, th=None):

        faithfulness_metrics = [
            f"compr_{th}",
            "aopc_compr",
            f"suff_{th}",
            "aopc_suff",
            "taud_loo",
        ]
        plausibility_metrics = ["auprc_plau"]
        from scipy import stats

        cols = list(df_eval.columns)

        # Higher is better
        show_higher_cols = [f"compr_{th}", "aopc_compr", "auprc_plau"]
        show_higher_cols = [i for i in show_higher_cols if i in df_eval.columns]
        for c in show_higher_cols:
            df_eval[f"{c}_r"] = (
                len(df_eval[c]) - stats.rankdata(df_eval[c], method="dense") + 1
            )
        # Close to 0 is better
        show_lower_cols = [f"suff_{th}", "aopc_suff", "taud_loo"]
        show_lower_cols = [i for i in show_lower_cols if i in df_eval.columns]

        for c in show_lower_cols:
            df_eval[f"{c}_r"] = stats.rankdata(df_eval[c], method="dense")

        # Just to show the columns in order
        # First the scores, then faithfulness_metrics, plausibility_metrics and then the others.
        show_cols = []
        for metric_show in faithfulness_metrics + plausibility_metrics:
            if metric_show in show_higher_cols + show_lower_cols:
                show_cols.extend([metric_show, f"{metric_show}_r"])

        output_cols = (
            list(score_cols)
            + show_cols
            + [c for c in cols if c not in list(score_cols) + show_cols]
        )
        return df_eval[output_cols]

    def compute_and_plot_compr_suff(
        self, text, explanations, thresholds, based_on="th", target=1, **kwargs
    ):

        compr = {}
        suff = {}
        for explainer_type in explanations.index:
            soft_score_explanation = explanations.loc[explainer_type].values
            compr[explainer_type] = self.compute_comprehensiveness_ths(
                text,
                soft_score_explanation,
                thresholds,
                based_on=based_on,
                target=target,
                **kwargs,
            )
            suff[explainer_type] = self.compute_sufficiency_ths(
                text,
                soft_score_explanation,
                thresholds,
                based_on=based_on,
                target=target,
                **kwargs,
            )
        import matplotlib.pyplot as plt

        fig, axs = plt.subplots(1, 2, figsize=(10, 3))
        for e in explanations.index:
            d = {k: v[1] for k, v in compr[e].items()}
            axs[0].plot(list(d.keys()), list(d.values()), label=e, marker="o")
            d = {k: v[1] for k, v in suff[e].items()}
            axs[1].plot(list(d.keys()), list(d.values()), label=e, marker="o")

        axs[0].set_title("Comprehensiveness (higher is better)")
        axs[1].set_title("Sufficiency (close to 0 is better)")
        plt.legend(bbox_to_anchor=(1.02, 1), loc="upper left")
        return fig
