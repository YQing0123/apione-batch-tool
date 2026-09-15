import cn.com.digitalhainan.apione.sdk.ContentBody;
import cn.com.digitalhainan.apione.sdk.HttpCaller;
import cn.com.digitalhainan.apione.sdk.HttpParameters;
import cn.com.digitalhainan.apione.sdk.HttpReturn;
import okhttp3.MediaType;

import java.util.Collections;
import java.util.Map;
import com.google.gson.Gson;
import com.google.gson.reflect.TypeToken;

/** Python 调用的 Java SDK 桥接层：所有鉴权和 HTTP 请求均由官方 JAR 完成。 */
public final class ApioneBatchSdkBridge {
    private ApioneBatchSdkBridge() {
    }

    public static void main(String[] args) {
        if (args.length != 9) {
            throw new IllegalArgumentException("用法：ApioneBatchSdkBridge <apiName> <region> <requestUrl> <method> <mediaType> <path> <headersJson> <queryJson> <jsonBody>");
        }
        String ak = requiredEnvironment("APIONE_AK");
        String sk = requiredEnvironment("APIONE_SK");
        String apiName = args[0];
        String region = args[1];
        String requestUrl = args[2];
        String method = args[3];
        String mediaType = args[4];
        String path = args[5];
        Gson gson = new Gson();
        Map<String, String> headers = gson.fromJson(args[6], new TypeToken<Map<String, String>>() { }.getType());
        Map<String, String> queryParams = gson.fromJson(args[7], new TypeToken<Map<String, String>>() { }.getType());
        String body = args[8];

        HttpParameters parameters = HttpParameters.builder()
                .api(apiName)
                .region(region)
                .accessKey(ak)
                .secretKey(sk)
                .requestUrl(requestUrl)
                .method(method)
                .mediaType(MediaType.parse(mediaType))
                .contentBody(new ContentBody(body))
                .headerParamsMap(headers == null ? Collections.<String, String>emptyMap() : headers)
                .queryParamsMap(queryParams == null ? Collections.<String, String>emptyMap() : queryParams)
                .path(path)
                .build();

        HttpReturn result = HttpCaller.getInstance().call(parameters);
        if (result == null || result.getResponse() == null) {
            throw new IllegalStateException("APIOne SDK 未返回响应");
        }
        System.out.println(result.getResponse());
    }

    private static String requiredEnvironment(String name) {
        String value = System.getenv(name);
        if (value == null || value.trim().isEmpty()) {
            throw new IllegalArgumentException("请配置环境变量：" + name);
        }
        return value.trim();
    }
}
