import time

from coppeliasim_zmqremoteapi_client import RemoteAPIClient


client = RemoteAPIClient()  # Connects to CoppeliaSim on this laptop.
sim = client.require("sim")

left = sim.getObject("/PioneerP3DX/leftMotor")
right = sim.getObject("/PioneerP3DX/rightMotor")
robot = sim.getObject("/PioneerP3DX")

sim.startSimulation()
try:
    sim.setJointTargetVelocity(left, 2)  # Both wheels forward.
    sim.setJointTargetVelocity(right, 2)
    time.sleep(3)

    sim.setJointTargetVelocity(left, -1)  # Turn in place.
    sim.setJointTargetVelocity(right, 1)
    time.sleep(2)

    print("Position:", sim.getObjectPosition(robot, -1))
finally:
    sim.stopSimulation()
