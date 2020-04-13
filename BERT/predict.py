from argparse import ArgumentParser
from typing import Dict
from constants import SENTIMENT_EXPERIMENTS_DIR, SENTIMENT_IMA_DATA_DIR, SENTIMENT_MLM_DATA_DIR, POMS_MLM_DATA_DIR, \
    POMS_GENDER_DATA_DIR, POMS_RACE_DATA_DIR, POMS_EXPERIMENTS_DIR, POMS_GENDER_DATASETS_DIR, POMS_RACE_DATASETS_DIR
from pytorch_lightning import Trainer, LightningModule
from BERT.networks import LightningBertPretrainedClassifier, BertPretrainedClassifier
from os import listdir, path
from glob import glob
from Timer import timer
from utils import GoogleDriveHandler, send_email, init_logger
import torch

# LOGGER = init_logger("OOB_training")


def get_checkpoint_file(ckpt_dir: str):
    for file in sorted(listdir(ckpt_dir)):
        if file.endswith(".ckpt"):
            return f"{ckpt_dir}/{file}"
    else:
        return None


def find_latest_model_checkpoint(models_dir: str):
    model_ckpt = None
    while not model_ckpt:
        model_versions = sorted(glob(models_dir), key=path.getctime)
        latest_model = model_versions.pop()
        model_ckpt_dir = f"{latest_model}/checkpoints"
        model_ckpt = get_checkpoint_file(model_ckpt_dir)
    return model_ckpt


def print_final_metrics(name: str, metrics: Dict, logger=None):
    if logger:
        logger.info(f"{name} Metrics:")
        for metric, val in metrics.items():
            logger.info(f"{metric}: {val:.4f}")
        logger.info("\n")
    else:
        print(f"{name} Metrics:")
        for metric, val in metrics.items():
            print(f"{metric}: {val:.4f}")
        print()


def main():
    parser = ArgumentParser()
    parser.add_argument("--treatment", type=str, required=False, default="gender", help="Specify treatment for experiments: adj, gender, gender_enriched, race, race_enriched")
    args = parser.parse_args()
    if "gender" in args.treatment or "race" in args.treatment:
        test_all_genderace_models(args.treatment)


@timer
def bert_treatment_test(model_ckpt, hparams, trainer, logger=None):
    if logger:
        logger.info(f"Testing model with {hparams['bert_params']['name']} LM")
    else:
        print(f"Testing model with {hparams['bert_params']['name']} LM")
    if isinstance(model_ckpt, LightningModule):
        model = model_ckpt
    else:
        model_ckpt_file = model_ckpt
        model = LightningBertPretrainedClassifier.load_from_checkpoint(model_ckpt_file)
    model.hparams.output_path = hparams["output_path"]
    if hparams["bert_params"]["bert_state_dict"]:
        model.bert_classifier.bert = BertPretrainedClassifier.load_frozen_bert(model.bert_classifier.bert_pretrained_model,
                                                                               hparams["bert_params"]["bert_state_dict"])
        model.bert_classifier.name = f"{hparams['bert_params']['name']}"
    model.freeze()
    trainer.test(model)
    print_final_metrics(hparams['bert_params']['name'], trainer.tqdm_metrics, logger)


@timer
def test_adj_models(factual_model_ckpt=None, counterfactual_model_ckpt=None):
    DOMAIN = "movies"
    TREATMENT = "adj"
    DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    HYPERPARAMETERS = {"bert_params": {}}
    # Factual OOB BERT Model training
    OUTPUT_DIR = f"{SENTIMENT_EXPERIMENTS_DIR}/{TREATMENT}/{DOMAIN}/COMPARE"
    trainer = Trainer(gpus=1 if DEVICE.type == "cuda" else 0,
                      default_save_path=OUTPUT_DIR,
                      show_progress_bar=True,
                      early_stop_callback=None)
    HYPERPARAMETERS["output_path"] = trainer.logger.experiment.log_dir.rstrip('tf')
    if not factual_model_ckpt:
        factual_model_ckpt = f"{SENTIMENT_EXPERIMENTS_DIR}/{TREATMENT}/{DOMAIN}/OOB_F/best_model/checkpoints"
    HYPERPARAMETERS["text_column"] = "review"
    HYPERPARAMETERS["bert_params"]["name"] = "OOB_F"
    HYPERPARAMETERS["bert_params"]["bert_state_dict"] = None
    bert_treatment_test(factual_model_ckpt, HYPERPARAMETERS, trainer)
    # Factual OOB BERT Model test with MLM LM
    HYPERPARAMETERS["bert_params"]["name"] = "MLM"
    HYPERPARAMETERS["bert_params"]["bert_state_dict"] = f"{SENTIMENT_MLM_DATA_DIR}/{DOMAIN}/model/pytorch_model.bin"
    bert_treatment_test(factual_model_ckpt, HYPERPARAMETERS, trainer)
    # Factual OOB BERT Model test with IMA LM
    HYPERPARAMETERS["bert_params"]["name"] = "IMA"
    HYPERPARAMETERS["bert_params"]["bert_state_dict"] = f"{SENTIMENT_IMA_DATA_DIR}/{DOMAIN}/model/pytorch_model.bin"
    bert_treatment_test(factual_model_ckpt, HYPERPARAMETERS, trainer)
    # Factual OOB BERT Model test with MLM LM (double_samples)
    HYPERPARAMETERS["bert_params"]["name"] = "MLM_double_samples"
    HYPERPARAMETERS["bert_params"]["bert_state_dict"] = f"{SENTIMENT_MLM_DATA_DIR}/double/{DOMAIN}/model/pytorch_model.bin"
    bert_treatment_test(factual_model_ckpt, HYPERPARAMETERS, trainer)
    # Factual OOB BERT Model test with IMA LM (double_adj)
    HYPERPARAMETERS["bert_params"]["name"] = "IMA_double_adj"
    HYPERPARAMETERS["bert_params"]["bert_state_dict"] = f"{SENTIMENT_IMA_DATA_DIR}/double/{DOMAIN}/model/pytorch_model.bin"
    bert_treatment_test(factual_model_ckpt, HYPERPARAMETERS, trainer)
    # CounterFactual OOB BERT Model training
    HYPERPARAMETERS["text_column"] = "no_adj_review"
    HYPERPARAMETERS["bert_params"]["name"] = "OOB_CF"
    HYPERPARAMETERS["bert_params"]["bert_state_dict"] = None
    if not counterfactual_model_ckpt:
        counterfactual_model_ckpt = f"{SENTIMENT_EXPERIMENTS_DIR}/{TREATMENT}/{DOMAIN}/OOB_CF/best_model/checkpoints"
    bert_treatment_test(counterfactual_model_ckpt, HYPERPARAMETERS, trainer)


@timer
def test_genderace_models_unit(task, treatment, group,
                               model_ckpt, hparams, trainer, logger):
    if "enriched" in treatment:
        state_dict_dir = "model_enriched"
    else:
        state_dict_dir = "model"
    if "gender" in treatment:
        TREATMENT = "Gender"
        pretrained_treated_model_dir = f"{POMS_GENDER_DATA_DIR}/{state_dict_dir}"
    else:
        TREATMENT = "Race"
        pretrained_treated_model_dir = f"{POMS_RACE_DATA_DIR}/{state_dict_dir}"
    label_size = 2
    if task == "POMS":
        label_column = f"{task}_label"
        label_size = 5
    elif task.split("_")[-1].lower() in treatment:
        label_column = f"{task.split('_')[-1]}_{group}_label"
    else:
        label_column = f"{task.split('_')[-1]}_label"
    # Group Task BERT Model training
    hparams["label_column"] = label_column
    hparams["bert_params"]["label_size"] = label_size
    hparams["text_column"] = f"Sentence_{group}"
    hparams["bert_params"]["name"] = f"{task}_{group}"
    logger.info("Label Column:", label_column)
    logger.info("Label Size:", label_size)
    logger.info("Text Column:", hparams["text_column"])
    logger.info("Task:", hparams["bert_params"]["name"])
    logger.info("Treatment:", treatment)
    if not model_ckpt:
        models_dir = f"{POMS_EXPERIMENTS_DIR}/{treatment}/{hparams['bert_params']['name']}/lightning_logs/*"
        model_ckpt = find_latest_model_checkpoint(models_dir)
        logger.info(f"Loading model for {treatment} {task}_{group} from: {model_ckpt}")
    hparams["bert_params"]["bert_state_dict"] = None
    bert_treatment_test(model_ckpt, hparams, trainer, logger)
    # Group Task BERT Model test with MLM LM
    hparams["bert_params"]["name"] = f"{task}_MLM_{group}"
    hparams["bert_params"]["bert_state_dict"] = f"{POMS_MLM_DATA_DIR}/{state_dict_dir}/pytorch_model.bin"
    bert_treatment_test(model_ckpt, hparams, trainer, logger)
    # Group Task BERT Model test with Gender/Race treated LM
    hparams["bert_params"]["name"] = f"{task}_{TREATMENT}_treated_{group}"
    hparams["bert_params"]["bert_state_dict"] = f"{pretrained_treated_model_dir}/pytorch_model.bin"
    bert_treatment_test(model_ckpt, hparams, trainer, logger)


@timer
def test_genderace_models(treatment="gender",
                          factual_poms_model_ckpt=None, counterfactual_poms_model_ckpt=None,
                          factual_gender_model_ckpt=None, counterfactual_gender_model_ckpt=None,
                          factual_race_model_ckpt=None, counterfactual_race_model_ckpt=None):
    DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    HYPERPARAMETERS = {"bert_params": {}}
    OUTPUT_DIR = f"{POMS_EXPERIMENTS_DIR}/{treatment}/COMPARE"
    trainer = Trainer(gpus=1 if DEVICE.type == "cuda" else 0,
                      default_save_path=OUTPUT_DIR,
                      show_progress_bar=True,
                      early_stop_callback=None)
    HYPERPARAMETERS["output_path"] = trainer.logger.experiment.log_dir.rstrip('tf')
    logger = init_logger(f"testing", HYPERPARAMETERS["output_path"])
    test_genderace_models_unit("POMS", treatment, "F", factual_poms_model_ckpt, HYPERPARAMETERS, trainer, logger)
    test_genderace_models_unit("POMS", treatment, "CF", counterfactual_poms_model_ckpt, HYPERPARAMETERS, trainer, logger)
    test_genderace_models_unit(f"CONTROL_Gender", treatment, "F", factual_gender_model_ckpt, HYPERPARAMETERS, trainer, logger)
    test_genderace_models_unit(f"CONTROL_Gender", treatment, "CF", counterfactual_gender_model_ckpt, HYPERPARAMETERS, trainer, logger)
    test_genderace_models_unit(f"CONTROL_Race", treatment, "F", factual_race_model_ckpt, HYPERPARAMETERS, trainer, logger)
    test_genderace_models_unit(f"CONTROL_Race", treatment, "CF", counterfactual_race_model_ckpt, HYPERPARAMETERS, trainer, logger)
    handler = GoogleDriveHandler()
    push_message = handler.push_files(HYPERPARAMETERS["output_path"])
    logger.info(push_message)
    send_email(push_message, treatment)


@timer
def test_all_genderace_models(treatment: str):
    test_genderace_models(treatment)
    test_genderace_models(f"{treatment}_biased_joy_gentle")
    test_genderace_models(f"{treatment}_biased_joy_aggressive")


if __name__ == "__main__":
    main()
