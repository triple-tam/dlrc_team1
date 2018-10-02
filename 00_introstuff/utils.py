import dlrc_control as ctrl
import numpy as np
from utils import *
import matplotlib.pyplot as plt
import mpl_toolkits.mplot3d.axes3d as p3
import time, sys, argparse
import py_at_broker as pab
import pandas as pd
import pickle



# deriving the transformation matrices using the DH params
# the Transformation matrix from (i-1) to (i) is
# there are actually two distinct conventions for denavit hartenberg
# which drastically change the result
# one is the "wkipedia" DH
# the other one is given on p. 83/75 of http://www.mech.sharif.ir/c/document_library/get_file?uuid=5a4bb247-1430-4e46-942c-d692dead831f&groupId=14040


def get_jointToCoordinates(thetas, trueCoordinates=None, untilJoint=None):
    '''
    gets coordinates of tip of end-effector depending on the 7 joint orientations
    params:
    thetas: list of joint orientations, length 7
    trueCoordinates: include this if want to calc difference from true coordinates, derived from cwhere
    returns:
    Tproduct: transformation matrix of the 7th joint/end-effector to the robot base wcs'''

    Tlist = []
    Tproduct = np.eye(4, 4)

    # for 7 joints
    aa = [0, 0, 0, 0.0825, -0.0825, 0, 0.088, 0]
    dd = [0.333, 0, 0.316, 0, 0.384, 0, 0, 0.107]
    alphas = [0, -np.pi / 2, np.pi / 2, np.pi / 2, -np.pi / 2, np.pi / 2, np.pi / 2, 0]
    if type(thetas) != list: thetas = thetas.tolist()
    thetas += [0]
    for a, d, alpha, theta in zip(aa, dd, alphas, thetas):
        T = np.array([[np.cos(theta), -np.sin(theta), 0, a],
                      [np.cos(alpha) * np.sin(theta), np.cos(alpha) * np.cos(theta), -np.sin(alpha),
                       -np.sin(alpha) * d],
                      [np.sin(theta) * np.sin(alpha), np.cos(theta) * np.sin(alpha), np.cos(alpha), np.cos(alpha) * d],
                      [0, 0, 0, 1]])

        # make sure this is a proper transformation matrix composed of a rotation and translational part:
        if not np.isclose(T[0:3, 0:3].T, np.linalg.inv(T[0:3, 0:3]), 1e-4, 1e-4).all(): raise ValueError(
            'transformation matrix invalid')

        Tproduct = np.dot(Tproduct, T)
        Tlist.append(T)

    # for end-effector
    Tee = np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0.11], [0, 0, 0, 1]])
    #     Tee = np.array([[1,0,0,0.11],[0,1,0,0],[0,0,1,0],[0,0,0,1]])
    Tlist.append(Tee)
    Tproduct = np.dot(Tproduct, Tee)  # transformation matrix from robot base to ~end-effector

    EE_coord = Tproduct.dot(np.array([0, 0, 0, 1]))
    assert (EE_coord[-1] == 1.00)

    if trueCoordinates:
        print('difference of ', np.sqrt(sum((trueCoordinates - EE_coord[:3]) ** 2)))

    if untilJoint:
        Tjoint = np.eye(4, 4)
        for T in Tlist[:untilJoint]:
            Tjoint = np.dot(Tjoint, T)
    else:
        Tjoint = None

    return Tproduct, Tlist, Tjoint, EE_coord





def img_to_ccs(depth_image, principal_point, camera_resolution, skip, rgb_image):

    depth_image = np.array(depth_image) / 1000

    ccs_points = []
    rgb_colors = []
    for x in np.arange(1,camera_resolution[1], skip):
        for y in np.arange(0,camera_resolution[0], skip):
            a = np.sin((x-principal_point[1])/camera_resolution[1] * 91.2 * np.pi/180) /1.45
            b = np.sin((y-principal_point[0])/camera_resolution[0] * 65.5 * np.pi/180) /1.45
            #a = ((x-principal_point[1])/0.00193) * 2.32
            #b = ((y - principal_point[0]) / 0.00193) * 2.32
            #a = (x - principal_point[1]) / camera_resolution[1] * 0.30
            #b = (y - principal_point[0]) / camera_resolution[0] * 0.30
            #a = (x - principal_point[1]) / camera_resolution[1]
            #b = (y - principal_point[0]) / camera_resolution[0]
            ccs_point = depth_image[y,x] * np.array([a,b,1/1.015,0]) #1.035
            ccs_point[-1] = 1
            ccs_points.append(ccs_point)

            rgb_colors.append([rgb_image[y*2,x*2]]) # rgb image is double the resolution of depth

    ccs_points = np.array(ccs_points)
    rgb_colors = np.array(rgb_colors).squeeze(axis=1)

    return ccs_points, rgb_colors


def grab_image(broker=None):

    if not broker: # either do these initialization steps inside or outside function, but either way happens just once
        broker = pab.broker("tcp://localhost:51468")
        broker.request_signal("realsense_images", pab.MsgType.realsense_image)

    img1 = broker.recv_msg("realsense_images", -1)
    img1_rgb = img1.get_rgb()
    img1_rgbshape = img1.get_shape_rgb()
    img1_rgb = img1_rgb.reshape(img1_rgbshape)

    img1_depth = img1.get_depth()
    img1_depthshape = img1.get_shape_depth()
    img1_depth = img1_depth.reshape(img1_depthshape)

    return img1_rgb, img1_depth



# calibration matrices of the sensors

Teecamera = np.eye(4)
# bc the DH transformation matrix only accounts for the translation of the flange from j7 and not its rotation of 45 degrees
flange_angle = 45 * np.pi/180
Teecamera[:2,:2] = [[np.cos(flange_angle), -np.sin(flange_angle)],
                    [np.sin(flange_angle), np.cos(flange_angle)]]
Teecamera[:3,3] = [ 0.0145966,  -0.05737133, -0.03899948]

transformation_values = {
    'lidar0': {
        'joint_number': 6,
        'params': [ 0.01807252,  0.00734784,  0.03238632, -0.09107123, -0.02269616,  0.93559049],
        'color': 'b'
    },
    'lidar1': {
        'joint_number': 5,
        'params': [-0.05722147,  0.06772991,  0.01825852, -0.91996184,  0.0048846,   0.28052113],
        'color': 'g'
    },
    'lidar2': {
        'joint_number': 5,
        'params': [-0.00359566,  0.06660305,  0.05624735,  0.11309117, -0.05526625, 0.99773982],
        'color': 'g'
    },
    'lidar3': {
        'joint_number': 5,
        'params': [0.05837634, 0.06860064, 0.00909611, 0.97177872, 0.02883462, 0.01135049],
        'color': 'g'
    },
    'lidar4': {
        'joint_number': 5,
        'params': [-0.0108765,   0.14202307, -0.00465494, -0.03576995,  0.93133041, -0.19942613],
        'color': 'g'
    },
    'lidar5': { # norm is 0.90
        'joint_number': 4,
        'params': [ 4.88563513e-03, -1.89098969e-02,  1.36769654e-01,  5.56412658e-04, 9.83044223e-02,  8.99031664e-01],
        'color': 'y'
    },
    'lidar6': { # norm is 0.94
        'joint_number': 3,
        'params': [ 0.13860735,  0.06645231, -0.01411203,  0.84711676,  0.08764442, -0.38833656],
        'color': 'k'
    },
    'lidar7': { # norm is 0.90
        'joint_number': 3,
        'params': [0.01089152, 0.06571974, 0.18947445, 0.37677934, 0.09777476, 0.81897023],
        'color': 'k'
    },
    'lidar8': { # norm is 0.88
        'joint_number': 3,
        'params': [ 0.09101747,  0.14465324, -0.09750162, -0.0286078,   0.83755668,  0.26530791],
        'color': 'k'
    },
    'camera': {
        'joint_number': 9, # EE
        'transformation_matrix': Teecamera, # Teecamera[:3,2] = [0.07378408, 0.02544135, 1.00632009] # minor rotation
        'color': 'r'
    }
}

for l in transformation_values:
    if l == 'camera': continue
    params = transformation_values[l]['params']
    T_j_l = np.zeros((4, 4))
    T_j_l[0:3, 2] = params[3:6]
    T_j_l[0:3, 3] = params[:3]
    T_j_l[3, 3] = 1
    transformation_values[l]['transformation_matrix'] = T_j_l

def get_calibration_values(sensor):
    return transformation_values[sensor]['joint_number'], transformation_values[sensor]['transformation_matrix']



def redraw_camera(Teecamera, joint, depth, rgb, principal_point, camera_resolution, bufsize, points_in_buffer, colors_in_buffer, i, box=None, detailed=False):
    '''assumes subplot axes to already exist'''

    T0ee, _,_,_ = get_jointToCoordinates(thetas=joint)
    T0cam = np.dot(T0ee, Teecamera)
    # K = np.array([[focal_length,0,principal_point[1]/ camera_resolution[1]],
    #              [0, focal_length, principal_point[0]/ camera_resolution[0]],
    #              [0,0,1]])
    # T0caminternal = np.dot(T0cam, np.linalg.inv(K))
    ccs_points, point_colors = img_to_ccs(depth, principal_point, camera_resolution, skip=13, rgb_image=rgb)
    wcs_points = [np.dot(T0cam, ccs_point) for ccs_point in ccs_points]
    points_in_buffer[i % bufsize, :,:] = wcs_points
    colors_in_buffer[i % bufsize, :,:] = np.array(point_colors).reshape(-1,3)/255
    colors_in_buffer = colors_in_buffer.reshape(-1,3)
    if colors_in_buffer.shape[0] == 1: colors_in_buffer = np.squeeze(colors_in_buffer, axis=0)


    #ax1.scatter(list(zip(*wcs_points))[0], list(zip(*wcs_points))[1], list(zip(*wcs_points))[2], s=50, c='b', alpha=0.1)
    ax1.clear()
    ax1.scatter(points_in_buffer[:,:,0], points_in_buffer[:,:,1], points_in_buffer[:,:,2], s=50, c=colors_in_buffer, alpha=0.5)
    colors_in_buffer = colors_in_buffer.reshape((bufsize, -1, 3))
    #ax1.axis('equal')
    ax1.set_xlim(-1, 1)
    ax1.set_ylim(-0.5,0.5)
    ax1.set_zlim(-0.1, 1.5)
    ax1.set_xlabel('x')
    ax1.set_ylabel('y')
    ax1.set_zlabel('z')

    camera_origin = np.dot(T0cam, np.array([0,0,0,1]))
    camera_target = np.dot(T0cam, np.array([0,0,1,1])*depth[principal_point[1], principal_point[0]]/1000)
    ax1.quiver(camera_origin[0], camera_origin[1], camera_origin[2],
               camera_target[0], camera_target[1], camera_target[2],
               color='r')

    im = ax2.imshow(depth, origin='lower')
    plt.colorbar(im, cax=ax2)
    ax2.set_xlim(ax2.get_xlim()[::-1])

    ax3.clear()
    ax3.imshow(rgb)
    ax3.set_xlim(ax3.get_xlim()[::-1])
    ax3.set_ylim(ax3.get_ylim()[::-1])

    if detailed:
        ax4.clear()
        ax4.hist(list(zip(*wcs_points))[0], bins=80)
        ax4.set_title('x values in wcs')

        ax5.clear()
        ax5.hist(list(zip(*wcs_points))[1], bins=80)
        ax5.set_title('y values in wcs')

        ax6.clear()
        ax6.hist(list(zip(*wcs_points))[2], bins=80)
        ax6.set_title('z values in wcs')

        ax7.clear()
        ax7.scatter(list(zip(*wcs_points))[0], list(zip(*wcs_points))[1])
        ax7.set_position([box.x0, box.y0, box.width * 0.5, box.height * 1.0])
        ax7.set_xlim(ax7.get_xlim()[::-1])
        ax7.set_ylim(ax7.get_ylim()[::-1])


    return points_in_buffer, colors_in_buffer