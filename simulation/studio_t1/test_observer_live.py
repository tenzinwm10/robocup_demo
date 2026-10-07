"""Exercise passive Linux packet capture and the ROS GameController feed in isolation."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import yaml
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from rosgraph_msgs.msg import Clock
from communications_observer import HERE, ROOT

def main():
    parameters=yaml.safe_load((ROOT/'src/brain/config/config.yaml').read_text())['brain_node']['ros__parameters']
    subprocess.run(['g++','-std=c++17','-O2','-I'+str(ROOT/'src/brain/include'),
        '-I'+str(ROOT/'src/booster_ros2_interface/include/booster_interface'),str(HERE/'team_fixture.cpp'),
        str(ROOT/'src/brain/src/team_communication_protocol.cpp'),'-o','/tmp/observer-fixture'],check=True,capture_output=True)
    fixtures=json.loads(subprocess.check_output(['/tmp/observer-fixture'],input=parameters['communication']['team_secret_hex'].encode()))
    players=[{'penalty':'NONE','secsTillUnpenalised':0,'warnings':0,'cautions':0} for _ in range(20)]
    team={'teamNumber':1,'fieldPlayerColour':1,'goalkeeperColour':0,'goalkeeper':1,'score':0,
          'penaltyShot':0,'singleShots':0,'messageBudget':12000,'players':players}
    referee={'version':19,'packetNumber':1,'playersPerTeam':3,'competitionType':'LARGE','stopped':False,
        'gamePhase':'NORMAL','state':'PLAYING','setPlay':'CORNER_KICK','firstHalf':True,'kickingTeam':1,
        'secsRemaining':600,'secondaryTime':0,'teams':[team,dict(team,teamNumber=2)]}
    rclpy.init(args=[]);node=Node('observer_test_input')
    publisher=node.create_publisher(String,'/soccer/game_controller',10)
    clock_pub=node.create_publisher(Clock,'/clock',10)
    sender=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);sender.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1)
    with tempfile.TemporaryDirectory() as directory:
        output=open(Path(directory)/'observer.log','w')
        process=subprocess.Popen([sys.executable,str(HERE/'communications_observer.py'),'--logs',directory],
                                 stdout=output,stderr=subprocess.STDOUT)
        try:
            start=time.monotonic(); snapshot=None; index=0
            while time.monotonic()-start<30:
                publisher.publish(String(data=json.dumps(referee)))
                clock=Clock();clock.clock.sec=10+int(time.monotonic()-start);clock_pub.publish(clock)
                for kind in ('discovery','state'): sender.sendto(bytes.fromhex(fixtures[kind]),('127.255.255.255',10001))
                rclpy.spin_once(node,timeout_sec=.05);time.sleep(.1);index+=1
                path=Path(directory)/'communications.json'
                if path.exists():
                    snapshot=json.loads(path.read_text())
                    if snapshot.get('referee') and len(snapshot['radio']['streams'])>=2: break
                if process.poll() is not None:
                    output.flush();raise RuntimeError(Path(directory,'observer.log').read_text())
            assert snapshot and snapshot['capture_status']=='capturing', snapshot
            assert snapshot['radio']['streams']['1/2/state']['latest']['ball_owner']==2
            assert snapshot['referee']['translated']['version']==20
            assert snapshot['referee']['translated']['set_play']==6
            assert snapshot['referee']['raw']['version']==19
            assert snapshot['referee_errors']==0
            assert snapshot['limits']['effective_tactical_timeout_ms']==1600
            print(json.dumps({'result':'PASS','passive_udp_capture':True,'original_decoder_crc_auth':True,
                              'raw_v19_and_translated_v20':True,'brain_packet_routes_untouched':True}))
        finally:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            output.close();sender.close();node.destroy_node();rclpy.shutdown()

if __name__=='__main__':main()
