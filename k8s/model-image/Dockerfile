# A surrogate model as its own image (owner 2026-10-20): the flask pod's initContainer checks MD5SUMS and copies
# /model into an emptyDir the solver reads as SURROGATE_MODEL_DIR. busybox (pinned) only for md5sum and cp; the model
# files never enter git or the flask image.
FROM busybox:1.36.1@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662
COPY model/ /model/
USER 1000:1000
