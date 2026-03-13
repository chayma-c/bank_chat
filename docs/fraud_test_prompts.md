# Fraud Detection Agent — Test Prompts

Test prompts for the fraud detection agent. The dataset contains three IBANs: `IBAN_FR123`, `IBAN_RU789`, `IBAN_TN456`.

---

## Fraud Analysis (French)

```
Analyse les fraudes pour IBAN_FR123
```
```
Vérifie s'il y a des anomalies sur le compte IBAN_TN456
```
```
Détecte les transactions suspectes sur IBAN_RU789
```
```
Est-ce qu'il y a du blanchiment d'argent sur IBAN_FR123 ?
```
```
Fais une analyse AML complète sur IBAN_RU789
```
```
Y a-t-il un risque de fraude sur le compte IBAN_TN456 ?
```
```
Vérifie les transactions bizarres sur IBAN_FR123
```
```
Analyse le risque sur IBAN_RU789, je pense qu'il y a des anomalies
```
```
Faut-il faire une déclaration TRACFIN pour IBAN_RU789 ?
```

## Fraud Analysis (English)

```
Check suspicious activity on IBAN_FR123
```
```
Run a fraud check on IBAN_RU789
```
```
Detect anomalies for account IBAN_TN456
```
```
Is there any money laundering risk on IBAN_RU789?
```
```
Analyze fraud risk for IBAN_FR123
```
```
Any AML alerts on IBAN_TN456?
```

## Export Transactions

```
Exporte toutes les transactions pour IBAN_FR123 en Excel
```
```
Télécharge le relevé de IBAN_TN456
```
```
Download all transactions for IBAN_RU789
```
```
Génère un historique des transactions pour IBAN_FR123
```
```
Export transaction history for IBAN_TN456
```
```
Generate a statement for IBAN_RU789
```

## Missing IBAN (should ask for one)

```
Analyse les fraudes svp
```
```
Check for suspicious transactions
```
```
Y a-t-il des anomalies ?
```
```
Run a fraud analysis
```
```
Détecte les fraudes
```

## Edge Cases & Tricky Inputs

```
Vérifie les fraudes sur FR7630001007941234567890185
```
```
Fraud check on FR76 3000 1007 9412 3456 7890 185
```
```
IBAN_FR123 export + fraud check
```
```
J'ai vu des mouvements suspects la nuit sur IBAN_FR123, vérifie
```
```
Il y a des virements vers la Russie depuis IBAN_FR123, c'est normal ?
```
```
Mon compte IBAN_TN456 a des transactions crypto, c'est risqué ?
```
```
Est-ce que TeamViewer a été utilisé sur le compte IBAN_FR123 ?
```
```
Il y a trop de tentatives de connexion échouées sur IBAN_RU789
```
```
Quelqu'un utilise un VPN pour accéder au compte IBAN_TN456
```
```
Nouveau bénéficiaire avec gros virement sur IBAN_FR123, c'est suspect ?
```

## Non-Fraud (should NOT route to fraud agent)

```
Quel est mon solde ?
```
```
Je veux faire un virement de 500€
```
```
Ma carte est bloquée, que faire ?
```
```
What are your opening hours?
```
```
Comment ouvrir un compte épargne ?
```
