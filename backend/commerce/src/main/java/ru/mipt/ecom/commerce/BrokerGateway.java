package ru.mipt.ecom.commerce;

import com.rabbitmq.client.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicBoolean;
import org.springframework.amqp.rabbit.connection.CachingConnectionFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
class BrokerGateway {
    final CachingConnectionFactory factory;
    BrokerGateway(@Value("${spring.rabbitmq.host:localhost}") String host,@Value("${spring.rabbitmq.username:commerce}") String user,@Value("${spring.rabbitmq.password:local-rabbit-password}") String password) {
        factory=new CachingConnectionFactory(host); factory.setUsername(user); factory.setPassword(password);
        factory.getRabbitConnectionFactory().setChannelRpcTimeout(3000);
        factory.setConnectionTimeout(1000); factory.setRequestedHeartBeat(10);
    }
    Channel channel() { return factory.createConnection().createChannel(false); }
    static void publish(Channel ch,String exchange,String routing,byte[] body,Map<String,Object> headers) throws Exception {
        ch.confirmSelect(); AtomicBoolean returned=new AtomicBoolean();
        ReturnListener listener=(code,text,ex,key,properties,bytes)->returned.set(true);
        ch.addReturnListener(listener);
        try {
            ch.basicPublish(exchange,routing,true,new AMQP.BasicProperties.Builder().deliveryMode(2).contentType("application/json").headers(headers).build(),body);
            ch.waitForConfirmsOrDie(3000);
            if(returned.get()) throw new IllegalStateException("Unroutable message");
        } finally { ch.removeReturnListener(listener); }
    }
    private long topologyUntil;
    synchronized void declare() throws Exception {
        if(System.nanoTime()<topologyUntil) return;
        try(var ch=channel()) {
            ch.exchangeDeclare("commerce.events","direct",true); ch.exchangeDeclare("commerce.dead","direct",true);
            for(String queue:List.of("search","notifications")) {
                ch.queueDeclare(queue+".dlq",true,false,false,Map.of("x-queue-type","quorum"));
                ch.queueBind(queue+".dlq","commerce.dead",queue);
                ch.queueDeclare(queue,true,false,false,Map.of("x-queue-type","quorum","x-dead-letter-exchange","commerce.dead","x-dead-letter-routing-key",queue,"x-delivery-limit",10,"x-overflow","reject-publish","x-dead-letter-strategy","at-least-once"));
                ch.queueBind(queue,"commerce.events",queue.equals("search")?"Product":"Order");
            }
        }
        topologyUntil=System.nanoTime()+java.time.Duration.ofSeconds(30).toNanos();
    }
    void invalidateTopology() { topologyUntil=0; }
    int replay(String queue,int limit) throws Exception {
        if(!List.of("search","notifications").contains(queue)) throw new CommerceService.ApiFailure(400,"INVALID_QUEUE");
        int count=0;
        try(var ch=channel()) {
            while(count<limit) {
                var message=ch.basicGet(queue+".dlq",false); if(message==null) break;
                publish(ch,"",queue,message.getBody(),Map.of());
                ch.basicAck(message.getEnvelope().getDeliveryTag(),false); count++;
            }
        }
        return count;
    }
}
