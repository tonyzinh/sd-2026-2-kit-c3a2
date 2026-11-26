"""
Embeddings por hashing: texto -> vetor de dimensao fixa, offline e sem estado.

Diferente do TF-IDF do C2.A2, nao precisa reajustar vocabulario quando o corpus
muda: qualquer replica produz o mesmo vetor para o mesmo texto.
"""
from sklearn.feature_extraction.text import HashingVectorizer

DIMENSAO = 512

_vetorizador = HashingVectorizer(n_features=DIMENSAO, ngram_range=(1, 2),
                                 strip_accents="unicode", norm="l2",
                                 alternate_sign=False)


def vetorizar(textos):
    return _vetorizador.transform(textos).toarray().tolist()
