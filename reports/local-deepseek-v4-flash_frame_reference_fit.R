# TaskBound inference cross-check (plan §11.3).
#
# `glmm.py` is a hand-rolled regularized mixed-effects fit, tested against
# synthetic data with known coefficients. That is the right test and it is not
# the question a reader asks, which is why not lme4. This script refits ONE
# registered model in an established implementation so the agreement can be
# published beside the release.
#
# The gate is that the comparison is performed and printed, not that the two
# agree exactly: they regularize differently, and any disagreement beyond the
# declared tolerance is explained rather than hidden.
library(lme4)
frame <- read.csv("local-deepseek-v4-flash_frame.csv")
fit <- glmer(
  compliance ~ condition * entry_point * induced_action + task + model_family +
    (1 | request_family_paraphrase) + (1 | injection_id) + (1 | placement_id),
  data = frame, family = binomial, control = glmerControl(optimizer = "bobyqa")
)
print(summary(fit))
print(as.data.frame(VarCorr(fit)))
