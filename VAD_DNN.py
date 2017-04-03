import tensorflow as tf
import numpy as np
import utils as utils
import re
import data_reader_bDNN as dr
import os
import matplotlib.pyplot as plt
import matplotlib.image as mpimg

FLAGS = tf.flags.FLAGS

tf.flags.DEFINE_string('mode', "train", "mode : train/ test [default : train]")

file_dir = "/home/sbie/github/VAD_KJT/Datafolder/SE_TIMIT_MRCG_0328"
input_dir = file_dir
output_dir = file_dir + "/Labels"

valid_file_dir = "/home/sbie/github/VAD_KJT/Datafolder/NX_TIMIT_MRCG"
valid_input_dir = valid_file_dir + "/Babble"
valid_output_dir = valid_file_dir + "/Babble/Labels"

norm_dir = input_dir

logs_dir = "/home/sbie/github/VAD_bDNN3/logs"

reset = True  # remove all existed logs and initialize log directories
device = '/gpu:0'

if FLAGS.mode is 'test':
    reset = False

if reset:

    os.popen('rm -rf ' + logs_dir + '/*')
    os.popen('mkdir ' + logs_dir + '/train')
    os.popen('mkdir ' + logs_dir + '/valid')


learning_rate = 0.0001
eval_num_batches = 2e5
SMALL_NUM = 1e-4
max_epoch = int(1e4)
dropout_rate = 0.5

decay = 0.9  # batch normalization decay factor
w = 19  # w default = 19
u = 9  # u default = 9
eval_th = 0.5
th = 0.5
num_hidden_1 = 512
num_hidden_2 = 512

batch_size = 4096 + 2*w # batch_size = 32
valid_batch_size = batch_size

assert (w-1) % u == 0, "w-1 must be divisible by u"

num_features = 768  # MRCG feature
bdnn_winlen = (((w-1) / u) * 2) + 3

bdnn_inputsize = int(bdnn_winlen * num_features)
bdnn_outputsize = 2


def affine_transform(x, output_dim, name=None):
    """
    affine transformation Wx+b
    assumes x.shape = (batch_size, num_features)
    """

    w = tf.get_variable(name + "_w", [x.get_shape()[1], output_dim], initializer=tf.truncated_normal_initializer(stddev=0.02))
    b = tf.get_variable(name + "_b", [output_dim], initializer=tf.constant_initializer(0.0))

    return tf.matmul(x, w) + b


def inference(inputs, keep_prob, is_training=True):

    # initialization
    # h1_out = affine_transform(inputs, num_hidden_1, name="hidden_1")
    h1_out = utils.batch_norm_affine_transform(inputs, num_hidden_1, name="hidden_1", decay=decay, is_training=is_training)
    h1_out = tf.nn.relu(h1_out)
    h1_out = tf.nn.dropout(h1_out, keep_prob=keep_prob)

    # h2_out = utils.batch_norm_affine_transform(h1_out, num_hidden_2, name="hidden_2")
    h2_out = utils.batch_norm_affine_transform(h1_out, num_hidden_2, name="hidden_2", decay=decay, is_training=is_training)
    h2_out = tf.nn.relu(h2_out)
    h2_out = tf.nn.dropout(h2_out, keep_prob=keep_prob)

    logits = affine_transform(h2_out, 2, name="output")

    return logits


def train(loss_val, var_list):

    lrDecayRate = .96
    lrDecayFreq = 200
    momentumValue = .9

    global_step = tf.Variable(0, trainable=False)
    lr = tf.train.exponential_decay(learning_rate, global_step, lrDecayFreq, lrDecayRate, staircase=True)

    # define the optimizer
    # optimizer = tf.train.MomentumOptimizer(lr, momentumValue)
    optimizer = tf.train.AdagradOptimizer(learning_rate)
    #
    # optimizer = tf.train.AdamOptimizer(lr)
    grads = optimizer.compute_gradients(loss_val, var_list=var_list)

    return optimizer.apply_gradients(grads, global_step=global_step)


def bdnn_prediction(bdnn_batch_size, logits, threshold=th):

    result = np.zeros((bdnn_batch_size, 1))
    indx = np.arange(bdnn_batch_size) + 1
    indx = indx.reshape((bdnn_batch_size, 1))
    indx = utils.bdnn_transform(indx, w, u)
    indx = indx[w:(bdnn_batch_size-w), :]
    indx_list = np.arange(w, bdnn_batch_size - w)
    for i in indx_list:
        indx_temp = np.where((indx-1) == i)
        pred = logits[indx_temp]
        pred = np.sum(pred)/pred.shape[0]
        result[i] = pred

    result = np.trim_zeros(result)
    result = result >= threshold

    return result.astype(int)


def evaluation(m_valid, valid_data_set, sess, eval_batch_size, num_batches=eval_num_batches):

    avg_valid_cost = 0.
    avg_valid_accuracy = 0.
    itr_sum = 0.

    while True:

        valid_inputs, valid_labels = valid_data_set.next_batch(eval_batch_size)

        if valid_data_set.eof_checker():
            valid_data_set.reader_initialize()
            print('Valid data reader was initialized!')  # initialize eof flag & num_file & start index
            break

        one_hot_labels = valid_labels.reshape((-1, 1))
        one_hot_labels = dense_to_one_hot(one_hot_labels, num_classes=2)

        feed_dict = {m_valid.inputs: valid_inputs, m_valid.labels: one_hot_labels,
                     m_valid.keep_probability: 1}

        valid_cost, valid_accuracy = sess.run([m_valid.cost, m_valid.accuracy], feed_dict=feed_dict)
        avg_valid_cost += valid_cost
        avg_valid_accuracy += valid_accuracy
        itr_sum += 1

    avg_valid_cost /= itr_sum
    avg_valid_accuracy /= itr_sum

    return avg_valid_cost, avg_valid_accuracy


def dense_to_one_hot(labels_dense, num_classes=2):
    """Convert class labels from scalars to one-hot vectors."""
    # copied from TensorFlow tutorial
    num_labels = labels_dense.shape[0]
    index_offset = np.arange(num_labels) * num_classes
    labels_one_hot = np.zeros((num_labels, num_classes))
    labels_one_hot.flat[(index_offset + labels_dense.ravel()).astype(int)] = 1
    return labels_one_hot.astype(np.float32)


class Model(object):
    def __init__(self, is_training=True):

        self.keep_probability = tf.placeholder(tf.float32, name="keep_probabilty")
        self.inputs = inputs = tf.placeholder(tf.float32, shape=[None, bdnn_inputsize],
                                              name="inputs")
        self.labels = labels = tf.placeholder(tf.float32, shape=[None, 2],
                                                            name="labels")

        # set inference graph
        self.logits = logits = inference(inputs, self.keep_probability, is_training=is_training)  # (batch_size, bdnn_outputsize)
        # set objective function
        pred = tf.argmax(logits, axis=1, name="prediction")
        pred = tf.cast(pred, tf.int32)
        truth = tf.cast(labels[:, 1], tf.int32)

        self.accuracy = tf.reduce_mean(tf.cast(tf.equal(pred, truth), tf.float32))
        self.cost = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(labels=labels, logits=logits))
        # self.cost = cost = tf.reduce_mean(cost)
        # cost = tf.reduce_sum(tf.square(labels - logits), axis=1)
        # self.cost = cost = tf.reduce_mean(cost)

        # self.sigm = tf.sigmoid(logits)
        # set training strategy
        trainable_var = tf.trainable_variables()
        self.train_op = train(self.cost, trainable_var)


def main(argv=None):
    #                               Graph Part                               #
    print("Graph initialization...")
    with tf.device(device):
        with tf.variable_scope("model", reuse=None):
            m_train = Model(is_training=True)
        with tf.variable_scope("model", reuse=True):
            m_valid = Model(is_training=False)

    print("Done")

    #                               Summary Part                             #

    print("Setting up summary op...")

    cost_ph = tf.placeholder(dtype=tf.float32)
    accuracy_ph = tf.placeholder(dtype=tf.float32)

    cost_summary_op = tf.summary.scalar("cost", cost_ph)
    accuracy_summary_op = tf.summary.scalar("accuracy", accuracy_ph)

    train_summary_writer = tf.summary.FileWriter(logs_dir + '/train/')
    valid_summary_writer = tf.summary.FileWriter(logs_dir + '/valid/', max_queue=2)
    print("Done")

    #                               Model Save Part                           #

    print("Setting up Saver...")
    saver = tf.train.Saver()
    ckpt = tf.train.get_checkpoint_state(logs_dir)
    print("Done")

    #                               Session Part                              #

    sess_config = tf.ConfigProto(allow_soft_placement=True, log_device_placement=False)
    sess_config.gpu_options.allow_growth = True
    sess = tf.Session(config=sess_config)

    if ckpt and ckpt.model_checkpoint_path:  # model restore
        print("Model restored...")
        saver.restore(sess, ckpt.model_checkpoint_path)
        print("Done")
    else:
        sess.run(tf.global_variables_initializer())  # if the checkpoint doesn't exist, do initialization

    data_set = dr.DataReader(input_dir, output_dir, norm_dir, w=w, u=u, name="train")  # training data reader initialization
    valid_data_set = dr.DataReader(valid_input_dir, valid_output_dir, norm_dir, w=w, u=u, name="valid")  # validation data reader initialization

    if FLAGS.mode is 'train':

        for itr in range(max_epoch):

            train_inputs, train_labels = data_set.next_batch(batch_size)
            # imgplot = plt.imshow(train_inputs)
            # plt.show()
            one_hot_labels = train_labels.reshape((-1, 1))
            one_hot_labels = dense_to_one_hot(one_hot_labels, num_classes=2)

            feed_dict = {m_train.inputs: train_inputs, m_train.labels: one_hot_labels,
                         m_train.keep_probability: dropout_rate}

            sess.run(m_train.train_op, feed_dict=feed_dict)

            if itr % 50 == 0 and itr >= 0:

                train_cost, train_accuracy = sess.run([m_train.cost, m_train.accuracy], feed_dict=feed_dict)

                print("Step: %d, train_cost: %.3f, train_accuracy=%3.3f" % (itr, train_cost, train_accuracy))

                train_cost_summary_str = sess.run(cost_summary_op, feed_dict={cost_ph: train_cost})
                train_accuracy_summary_str = sess.run(accuracy_summary_op, feed_dict={accuracy_ph: train_accuracy})
                train_summary_writer.add_summary(train_cost_summary_str, itr)  # write the train phase summary to event files
                train_summary_writer.add_summary(train_accuracy_summary_str, itr)

            if itr % 100 == 0 and itr >= 0:

                saver.save(sess, logs_dir + "/model.ckpt", itr)  # model save

                valid_cost, valid_accuracy = evaluation(m_valid, valid_data_set, sess, valid_batch_size)
                #
                print('')
                print("valid_cost: %.3f, valid_accuracy: %.3f" % (valid_cost, valid_accuracy))
                print('')
                valid_summary_str_cost = sess.run(cost_summary_op, feed_dict={cost_ph: valid_cost})
                valid_summary_str_accuracy = sess.run(accuracy_summary_op, feed_dict={accuracy_ph: valid_accuracy})
                valid_summary_writer.add_summary(valid_summary_str_cost, itr)
                valid_summary_writer.add_summary(valid_summary_str_accuracy, itr)

    elif FLAGS.mode is 'test':
        _, valid_accuracy = evaluation(m_valid, valid_data_set, sess, valid_batch_size)
        print("valid_accuracy = %.3f" % valid_accuracy)


if __name__ == "__main__":
    tf.app.run()



